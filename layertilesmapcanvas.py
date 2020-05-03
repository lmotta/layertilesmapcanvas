# -*- coding: utf-8 -*-
"""
Adaptation:
Sources from 'https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames'
- deg2num
- num2deg
Source from 'https://gis.stackexchange.com/questions/268890/get-current-zoom-level-from-qgis-map-canvas'
- getZoom

Source from 'https://gist.github.com/maptiler' 
- getQuadKey

See:
BING
http://ecn.t3.tiles.virtualearth.net/tiles/a{q}.jpeg?g=1
https://docs.microsoft.com/en-us/bingmaps/articles/bing-maps-tile-system
"""

import os, math, functools
import collections
import urllib.request, urllib.error
import multiprocessing
from concurrent import futures

from osgeo import gdal
gdal.UseExceptions()
from osgeo.gdalconst import GA_ReadOnly

from qgis.PyQt.QtCore import (
    Qt, QObject, QVariant,
    pyqtSignal, pyqtSlot
)
from qgis.PyQt.QtWidgets import (
    QWidget, QPushButton,
    QLabel, QLineEdit,
    QTabWidget, QRadioButton, QCheckBox,
    QComboBox,
    QVBoxLayout, QHBoxLayout
)

from qgis.core import (
    Qgis, QgsApplication, QgsProject,
    QgsGeometry, QgsRectangle,
    QgsRasterLayer, QgsVectorLayer, QgsFeature, QgsField,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsCoordinateTransformContext,
    QgsTask
)
from qgis.gui import (
    QgsGui,
    QgsMessageBar,
    QgsLayerTreeEmbeddedWidgetProvider,
    QgsFileWidget, QgsFilterLineEdit
)

from qgis import utils as QgsUtils

def createMemoryLayer(name, fields, sTypeGeometry, crs, filepathStyle=None):
    l_fields = [ f"field={k}:{v}" for k,v in fields.items() ]
    l_fields.insert( 0, f"{sTypeGeometry}?crs={crs.authid().lower()}" )
    l_fields.append('index=yes' )
    uri = '&'.join( l_fields )
    layer = QgsVectorLayer( uri, name, 'memory')
    if filepathStyle:
        layer.loadNamedStyle( filepathStyle )
    return layer

def getCRS_3857():
    wkt = 'PROJCS["WGS 84 / Pseudo-Mercator",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]],PROJECTION["Mercator_1SP"],PARAMETER["central_meridian",0],PARAMETER["scale_factor",1],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["metre",1,AUTHORITY["EPSG","9001"]],AXIS["X",EAST],AXIS["Y",NORTH],EXTENSION["PROJ4","+proj=merc +a=6378137 +b=6378137 +lat_ts=0.0 +lon_0=0.0 +x_0=0.0 +y_0=0 +k=1.0 +units=m +nadgrids=@null +wktext  +no_defs"],AUTHORITY["EPSG","3857"]]'
    crs = QgsCoordinateReferenceSystem()
    crs.createFromWkt( wkt )
    return crs


class TilesMapCanvas():
    MAXSCALEPERPIXEL = 156543.04
    INCHESPERMETER = 39.37
    CRS4326 = QgsCoordinateReferenceSystem('EPSG:4326')
    CRS3857 = getCRS_3857()
    def __init__(self, fieldNames):
        self.fieldNames = fieldNames
        self.mapCanvas = QgsUtils.iface.mapCanvas()
        self.ct =  QgsCoordinateTransform( self.CRS4326, self.CRS3857, QgsCoordinateTransformContext() )
        self.extentTiles = None

    def setExtentTiles(self, zoom):
        def getExtentMapCanvas():
            mapSettings = self.mapCanvas.mapSettings()
            crsCanvas = mapSettings.destinationCrs()
            extent = self.mapCanvas.extent()
            if self.CRS4326 != crsCanvas:
                ct =  QgsCoordinateTransform( crsCanvas, self.CRS4326, QgsCoordinateTransformContext() )
                return ct.transform( extent )
            return extent

        def deg2num(vlong, vlat, zoom):
            lat_rad = math.radians(vlat)
            n = 2.0 ** zoom
            xtile = int((vlong + 180.0) / 360.0 * n)
            ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
            return (xtile, ytile)

        extent = getExtentMapCanvas() # EPSG 4326
        tile_x1, tile_y1 = deg2num( extent.xMinimum(), extent.yMaximum(), zoom )
        tile_x2, tile_y2 = deg2num( extent.xMaximum(), extent.yMinimum(), zoom )
        ExtentTiles = collections.namedtuple('ExtentTiles', 'x1 y1 x2 y2')
        self.extentTiles = ExtentTiles( tile_x1, tile_y1, tile_x2, tile_y2 )

    @property
    def total(self):
        t = self.extentTiles
        return ( t.x2 - t.x1 + 1 ) * ( t.y2 - t.y1 + 1 )

    def __call__(self, zoom):
        def num2deg(xtile, ytile):
            n = 2.0 ** zoom
            vlong = xtile / n * 360.0 - 180.0
            lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
            vlat = math.degrees(lat_rad)
            return (vlong, vlat)
        
        def getRectTile(x, y):
            xMin, yMax = num2deg( x, y )
            xMax, yMin = num2deg( x+1, y+1 )
            rect = QgsRectangle( xMin, yMin, xMax, yMax )
            return self.ct.transform( rect )

        def getQuadKey(tileX, tileY):
            quadKey = ""
            for i in range( zoom, 0, -1):
                digit = 0
                mask = 1 << (i-1)
                if (tileX & mask) != 0:
                    digit += 1
                if (tileY & mask) != 0:
                    digit += 2
                quadKey += str( digit )
                
            return quadKey

        Tile = collections.namedtuple('Tile', 'x y z q rect')
        for x in range( self.extentTiles.x1, self.extentTiles.x2+1):
            for y in range( self.extentTiles.y1, self.extentTiles.y2+1):
                quadKey = getQuadKey( x, y )
                tile = Tile( x, y, zoom, quadKey, getRectTile( x, y ) ) # EPSG 3857
                yield tile


class TaskDownloadTiles(QgsTask):
    image = pyqtSignal(dict)
    finish = pyqtSignal(dict)
    progress = pyqtSignal()
    def __init__(self, name, zoom, infoFeatures, dirPath, hasVrt, slotData, slotFinished):
        def getMaxWorks():
            nCpu = multiprocessing.cpu_count()
            return nCpu if self.totalFeats > nCpu else int( self.totalFeats / 2 ) + 1

        super().__init__( self.__class__.__name__, QgsTask.CanCancel )
        self.name = name
        self.zoom = zoom
        self.infoFeatures = infoFeatures
        self.totalFeats = len( infoFeatures )
        self.countFeats = None
        self.countDownload = None
        self.hasCancel = None
        self.hasVrt = hasVrt
        self.filepathImagesVrt = []
        self.max_download_workers = getMaxWorks()
        self.future_to_info = None
        self.dirPath = dirPath
        self.driveTif = gdal.GetDriverByName('GTiff')
        self.wkt3857 = TilesMapCanvas.CRS3857.toWkt()
        self.image.connect( slotData )
        self.finish.connect( slotFinished )
        self.progress.connect( self.progressDownload )

    @pyqtSlot()
    def progressDownload(self):
        self.countFeats += 1
        value = self.countFeats / self.totalFeats * 100
        self.setProgress( value )

    # Overwrite QgsTask methods
    def run(self):
        def downloadImage(info):
            def getDataSource(src):
                try:
                    ds = gdal.Open( src, GA_ReadOnly )
                except RuntimeError:
                    ds = None
                    msg = f"Url '{info.url}': Error open image"
                    return { 'isOk': False, 'message': msg }
                return { 'isOk': True, 'ds': ds }

            def setGeoreference(ds):
                resX, resY = info.width / ds.RasterXSize, info.height / ds.RasterYSize
                args = (
                    info.ulX, resX, 0.0,
                    info.ulY, 0.0, -1*resY
                )
                ds.SetGeoTransform( args )
                ds.SetProjection( self.wkt3857 )

            filepath = os.path.join( self.dirPath, f"{info.name}.tif" )
            if os.path.isfile( filepath ):
                r = getDataSource( filepath )
                self.progress.emit()
                if not r['isOk']:
                    self.image.emit( { 'name': info.name, 'message': r['message'] } )
                    return
                r['ds'] = None
                if self.hasVrt:
                    self.filepathImagesVrt.append( filepath )
                else:
                    self.image.emit( { 'name': info.name, 'filepath': filepath } )
                return
            memfile = '/vsimem/temp'
            args = {
                'url': info.url,
                'headers': { 'User-agent': 'QGIS Plugin' }
            }
            request = urllib.request.Request( **args )
            with urllib.request.urlopen( request) as response:
                gdal.FileFromMemBuffer( memfile, response.read() )
            self.countDownload += 1
            self.progress.emit()
            r = getDataSource( memfile)
            if not r['isOk']:
                gdal.Unlink('memfile')
                self.image.emit( { 'name': info.name, 'message':r['message'] } )
            ds = self.driveTif.CreateCopy( filepath, r['ds'] )
            r['ds'] = None
            gdal.Unlink( 'memfile')
            setGeoreference( ds )
            ds = None
            if self.hasVrt:
                self.filepathImagesVrt.append( filepath )
            else:
                self.image.emit( { 'name': info.name, 'filepath': filepath } )

        self.countFeats = 0
        self.countDownload = 0
        self.hasCancel = False
        self.filepathImagesVrt.clear()
        args = ( self.max_download_workers, self.__class__.__name__ )
        with futures.ThreadPoolExecutor( *args ) as e:
            self.future_to_info = [ e.submit( downloadImage, info ) for info in self.infoFeatures ]
            futures.wait( self.future_to_info )
        return True

        # vrt_files = []
        # c_feat = 0
        # for info in getInfoFeatures():
        #     if self.isCanceled():
        #         self.result = { 'canceled': True, 'total': c_feat }
        #         return None
        #     data = { 'name': f"Z={info.z} X={info.x} Y={info.y}" }
        #     progress = c_feat / self.totalFeats * 100
        #     self.setProgress( progress )
        #     r, existsFile = downloadImage( info )
        #     if not existsFile:
        #         c_feat += 1
        #     if not r['isOk']:
        #         data['message'] = r['message']
        #         self.image.emit( data )
        #         continue
        #     if self.hasVrt:
        #         vrt_files.append( r['filepath'] )
        #         continue
        #     data['filepath'] = r['filepath']
        #     self.image.emit( data )
        # self.result = { 'canceled': False, 'total': c_feat }
        # if self.hasVrt: createVrt( vrt_files, info.z )
        # return True

    def cancel(self):
        self.hasCancel = True
        for future in self.future_to_info:
            if not future.done() and not future.running(): future.cancel()

    @pyqtSlot(bool)
    def finished(self, result=None):
        def createVrt():
            options = gdal.BuildVRTOptions( resampleAlg=gdal.GRIORA_NearestNeighbour )
            vrt = f"{self.name}_{self.zoom}.vrt"
            filepath = os.path.join( self.dirPath, vrt )
            _ds = gdal.BuildVRT( filepath, self.filepathImagesVrt, options=options )
            _ds = None
            name = f"{self.name} Z={self.zoom}"
            self.image.emit( { 'name': name, 'filepath': filepath } )

        if self.hasVrt:
            createVrt()
            self.filepathImagesVrt.clear()
        self.finish.emit( { 'canceled': self.hasCancel, 'total': self.countDownload } )


class LayerTilesMapCanvas(QObject):
    FIELDS = { 'x': 'integer', 'y': 'integer', 'z': 'integer', 'q': 'string(-1)' }
    changeZoom = pyqtSignal( int, int )
    finishProcess = pyqtSignal(dict) # { 'name', 'canceled', 'error', 'total' }
    def __init__(self, layer ):
        super().__init__()
        self._layer = layer
        self._frm_url, self.getUrl = None, None
        self._tilesCanvas = TilesMapCanvas( list( self.FIELDS.keys() ) )
        self.mapCanvas = QgsUtils.iface.mapCanvas()
        self.msgBar = QgsUtils.iface.messageBar()
        self.project = QgsProject.instance()
        self.taskManager = QgsApplication.taskManager()
        self.root = self.project.layerTreeRoot()
        self.nameGroupTiles = 'Tile images'
        self.ltgTiles = self.root.findGroup( self.nameGroupTiles )
        self._zoom = self._getZoom()
        self._tilesCanvas.setExtentTiles( self._zoom )
        self.currentTask = None
        self.msgRunnigTak = 'It is running process'
        self._connect()

    def _getZoom(self):
        dpi = QgsUtils.iface.mainWindow().physicalDpiX()
        scale = QgsUtils.iface.mapCanvas().scale()
        f =  dpi / scale
        two_power_zoom = f * TilesMapCanvas.INCHESPERMETER * TilesMapCanvas.MAXSCALEPERPIXEL
        r = math.log( two_power_zoom, 2 )
        return int( round( r, 0 ) )

    def _connect(self, connect=True):
        ss = {
            self.mapCanvas.scaleChanged: self.on_scaleChanged,
            self.project.layerWillBeRemoved: self.on_layerWillBeRemoved
        }
        if connect:
            for f in ss: f.connect( ss[ f ] )
            return
        for f in ss: f.disconnect( ss[ f ] )

    def _createGroupTiles(self):
        self.ltgTiles = self.root.addGroup( self.nameGroupTiles )
        self.ltgTiles.setItemVisibilityChecked( False )

    def _removeLayersTile(self):
        ltgTiles = self.root.findGroup( self.nameGroupTiles )
        if ltgTiles:
            self.project.removeMapLayers( ltgTiles.findLayerIds() )
            self.mapCanvas.refresh()

    @staticmethod
    def createLayer():
        filepath = f"{os.path.splitext(__file__)[0]}.qml"
        layer = createMemoryLayer('tiles', LayerTilesMapCanvas.FIELDS, 'Polygon', TilesMapCanvas.CRS3857, filepath  )
        return layer

    @property
    def totalTiles(self): return self._tilesCanvas.total

    @property
    def zoom(self): return self._zoom

    @zoom.setter
    def zoom(self, value):
        self._zoom = value
        self._tilesCanvas.setExtentTiles( value )

    @property
    def format_url(self): return self._frm_url

    @format_url.setter
    def format_url(self, value):
        self._frm_url = None
        if not bool(value):
            return
        totalZXY = functools.reduce(
            lambda a, b: value.find(f"{a}") + value.find(f"{b}"),
            'zxy'
        )
        if totalZXY < 0:
             self.getUrl = lambda info: value.format( q=info.q )
        else:
            self.getUrl = lambda info: value.format(
                z=info.z, x=info.x, y=info.y
            )
        Tile = collections.namedtuple('Tile', 'x y z q rect')
        info = Tile(0, 0, 1, '3', None)
        try:
            args = {
                'url': self.getUrl( info ),
                'headers': { 'User-agent': 'QGIS Plugin' }
            }
            request = urllib.request.Request( **args )
            _response = urllib.request.urlopen( request, timeout=5 )
        except ValueError as e:
            raise Exception( str(e) )
        except urllib.error.HTTPError as e:
            raise Exception( str(e) )
        except urllib.error.URLError as e:
            raise Exception( str(e) )
        self._frm_url = value

    def setLayerName(self):
        name  = f"Tiles Z={self._zoom}"
        if self._frm_url:
            name += ' URL'
        self._layer.setName( name )

    def setCustomProperty(self, key, value ):
        self._layer.setCustomProperty(key, value )

    @pyqtSlot(float)
    def on_scaleChanged(self, scale_):
        zoom = self._getZoom()
        if self._zoom != zoom:
            self.zoom = zoom # self._tilesCanvas.setExtentTiles
            self.changeZoom.emit( zoom, self.totalTiles )

    @pyqtSlot(str)
    def on_layerWillBeRemoved(self, layerId):
        if self._layer.id() == layerId:
            self._connect( False )
            self._layer, self._tilesCanvas = 2 * [ None ]

    # Emit finishProcess
    @pyqtSlot() # update
    def updateFeatures(self):
        def addExpField(expField):
            field = QgsField( expField, QVariant.String )
            sq, iniBraces, endBraces = "'{}"
            value = self._frm_url
            for c in 'xyzq':
                src = f"{iniBraces}{c}{endBraces}"
                dest = f"{sq} || {c} || {sq}"
                value = value.replace( src, dest )
            end_sq = f" || {sq}"
            if value.endswith( end_sq ):
                total = -1 * (len( end_sq ) )
                value = f"{sq}" + value[:total]
            else:
                value = f"{sq}{value}{sq}"
            self._layer.addExpressionField( value, field )

        def run(task, prov, totalTiles):
            c_tiles = 0
            for feat in self._tilesCanvas( self._zoom ):
                if task.isCanceled():
                    return { 'canceled': True, 'total': c_tiles }
                f = QgsFeature()
                geom = QgsGeometry.fromRect( feat.rect )
                f.setGeometry( geom )
                f.setAttributes( [ feat.x, feat.y, feat.z, feat.q ] )
                prov.addFeature( f )
                progress = c_tiles / totalTiles * 100
                task.setProgress( progress )
                c_tiles += 1
            return { 'canceled': False, 'total': c_tiles }

        def finished(exception, result=None):
            self._layer.updateExtents()
            self._layer.triggerRepaint()
            self.currentTask = None
            r = { 'name': 'update' }
            if exception:
                r['error'] = f"Exception, {exception}"
            r.update( result )
            self.finishProcess.emit( r )

        if self.currentTask:
            self.currentTask.cancel()
            return
        prov = self._layer.dataProvider()
        prov.truncate() # Delete all
        expField = 'url'
        idExpField = self._layer.fields().indexOf( expField )
        if idExpField > -1:
            self._layer.removeExpressionField( idExpField )
        if self._frm_url:
            addExpField( expField )
        # Task
        self.finishedTask = False
        args = {
            'description': f"{self.__class__.__name__}.populate",
            'function': run,
            'prov': prov,
            'totalTiles': self.totalTiles,
            'on_finished': finished
        }
        self.currentTask = QgsTask.fromFunction( **args )
        self.currentTask.setDependentLayers( [ self._layer ] )
        self.taskManager.addTask( self.currentTask )

    @pyqtSlot(str) # download
    def downloadTiles(self, name, dirPath, hasVrt):
        def getZoom_InfoFeatures(nameTile):
            InfoTile = collections.namedtuple('InfoTile', 'z x y q')
            fields = 'name url width height ulX ulY'
            InfoFeature = collections.namedtuple('InfoFeature', fields )
            infos = []
            featIterator = self._layer.getFeatures()
            zoom = next( featIterator )['z']
            featIterator.rewind()
            for feat in featIterator:
                infoTile = InfoTile( feat['z'], feat['x'], feat['y'], feat['q'] )
                name = f"{nameTile}_{infoTile.z}_{infoTile.x}_{infoTile.y}"
                url = self.getUrl( infoTile )
                e = feat.geometry().boundingBox()
                args = (
                    name, url,
                    e.width(), e.height(),
                    e.xMinimum(), e.yMaximum()
                )
                info = InfoFeature( *args )
                infos.append( info )
            return zoom, infos

        @pyqtSlot(dict)
        def add(dictFile):
            """
            dictFile{'name', 'filepath'}
            """
            if not self.root.findGroup( self.nameGroupTiles ):
                self._createGroupTiles()
            layer = QgsRasterLayer( dictFile['filepath'], dictFile['name'] )
            self.project.addMapLayer( layer, False )
            self.ltgTiles.addLayer( layer )

        @pyqtSlot(dict)
        def finished(result):
            self.currentTask = None
            r = { 'name': 'download' }
            r.update( result )
            self.finishProcess.emit( r )

        if self.currentTask:
            self.currentTask.cancel()
            return

        if not self.root.findGroup( self.nameGroupTiles ):
            self._createGroupTiles()
        self._removeLayersTile()
        self.finishedTask = False
        zoom, infoFeatures = getZoom_InfoFeatures( name )
        args = (
            name, zoom, infoFeatures,
            dirPath, hasVrt,
            add, finished
        )
        self.currentTask = TaskDownloadTiles( *args )
        self.currentTask.setDependentLayers( [ self._layer ] )
        self.taskManager.addTask( self.currentTask )

    @pyqtSlot(str) # count_images
    def getTotalImages(self, dirPath):
        def run(task, dirPath):
            files =  os.listdir( dirPath )
            images = [ os.path.join( dirPath, fr) for fr in files if fr.endswith('tif') ]
            total = len( images )
            del files; del images
            return { 'canceled': False, 'total': total }

        def finished(exception, result=None):
            self.currentTask = None
            r = { 'name': 'count_images' }
            if exception:
                r['error'] = f"Exception, {exception}"
            r.update( result )
            self.finishProcess.emit( r )

        if self.currentTask:
            self.currentTask.cancel()
            return
        # Task
        self.finishedTask = False
        args = {
            'description': f"{self.__class__.__name__}.getTotalImages",
            'function': run,
            'dirPath': dirPath,
            'on_finished': finished
        }
        self.currentTask = QgsTask.fromFunction( **args )
        self.taskManager.addTask( self.currentTask )

    @pyqtSlot(str) # remove_images
    def removeImages(self, dirPath):
        def run(task, dirPath, f_exts):
            def hasRemove( filepath):
                for ext in f_exts:
                    if filepath.endswith( ext ):
                        return True
                return False

            files = [ os.path.join(dirPath, f ) for f in os.listdir( dirPath ) ]
            if not bool( len( files ) ):
                return { 'canceled': False, 'total': 0 }
            c_tiles = 0
            for f in files:
                if not hasRemove( f ): continue 
                c_tiles += 1
                os.remove( f )
                if task.isCanceled():
                    return { 'canceled': True, 'total': c_tiles }
            return { 'canceled': False, 'total': c_tiles }

        def finished(exception, result=None):
            self._removeLayersTile()
            self.currentTask = None
            r = { 'name': 'remove_images' }
            if exception:
                r['error'] = f"Exception, {exception}"
            r.update( result )
            self.finishProcess.emit( r )

        if self.currentTask:
            self.currentTask.cancel()
            return
        # Task
        self.finishedTask = False
        args = {
            'description': f"{self.__class__.__name__}.removeImages",
            'function': run,
            'dirPath': dirPath,
            'f_exts': ('tif', 'tif.aux.xml', 'vrt'),
            'on_finished': finished
        }
        self.currentTask = QgsTask.fromFunction( **args )
        self.taskManager.addTask( self.currentTask )


class LayerTilesMapCanvasWidget(QWidget):
    KEYPROPERTY_URL = 'LayerTilesMapCanvas/url'
    KEYPROPERTY_DIR = 'LayerTilesMapCanvas/dir_tiles'
    def __init__(self, layer, registerLayers):
        def setGui():
            def createLayoutZoom():
                lyt = QHBoxLayout()
                _lbl = QLabel('Zoom:', self)
                lyt.addWidget( _lbl )
                cbZoom = QComboBox( self )
                cbZoom.addItems( [ str(n) for n in range(5, 19) ] )
                lyt.addWidget( cbZoom )
                lblTiles = QLabel('', self )
                lyt.addWidget( lblTiles )

                return lyt, cbZoom, lblTiles

            def createLayoutUrl():
                lyt = QVBoxLayout()
                lblName = QLabel('', self )
                lblName.setTextFormat( Qt.RichText )
                lyt.addWidget( lblName )
                leUrl = QgsFilterLineEdit(self, 'Enter with a url with ..{z}..{x}..{y}')
                leUrl.setToolTip('Tile server')
                lyt.addWidget( leUrl )
                return lyt, lblName, leUrl

            def createTabs():
                # Tab1
                lyt1 = QHBoxLayout()
                lytRadios = QVBoxLayout()
                rbUpdate = QRadioButton('Update', self )
                lytRadios.addWidget( rbUpdate )
                rbDownload = QRadioButton('Download', self )
                lytRadios.addWidget( rbDownload )
                lyt1.addLayout( lytRadios )
                lytOk = QVBoxLayout()
                btnOk = QPushButton( 'OK', self )
                lytOk.addWidget( btnOk )
                ckVrt = QCheckBox('Create VRT image', self )
                lytOk.addWidget( ckVrt )
                lyt1.addLayout( lytOk )
                tab1 = QWidget()
                tab1.setLayout( lyt1 )
                # Tab 2
                lyt2 = QVBoxLayout()
                wgtDir = QgsFileWidget(self )
                lyt2.addWidget( wgtDir )
                btnRemoveFiles = QPushButton('', self )
                lyt2.addWidget( btnRemoveFiles )
                tab2 = QWidget()
                tab2.setLayout( lyt2 )
                #
                tabs = QTabWidget(self)
                tabs.addTab( tab1, 'Update/Download')
                tabs.addTab( tab2, 'Download directory')

                return (
                    tabs,
                    rbUpdate, rbDownload, btnOk,
                    ckVrt, wgtDir, btnRemoveFiles
                )

            lytZoom, cbZoom, lblTiles = createLayoutZoom()
            lytUrl, lblName, leUrl = createLayoutUrl()
            (
                tabs,
                rbUpdate, rbDownload, btnOk,
                ckVrt, wgtDir, btnRemoveFiles
            ) = createTabs()
            # Layout
            lyt = QVBoxLayout()
            lyt.addLayout( lytZoom )
            lyt.addLayout( lytUrl )
            lyt.addWidget( tabs  )
            self.setLayout( lyt )
            # Create ItemsGui only these names
            names = (
                'cbZoom', 'lblTiles',
                'lblName', 'leUrl',
                'rbUpdate', 'rbDownload', 'btnOk',
                'ckVrt', 'wgtDir', 'btnRemoveFiles'
            )
            l_objs = locals()
            objs = tuple( l_objs[ name ] for name  in names )
            ItemsGui = collections.namedtuple('ItemsGui', names )
            return ItemsGui( *objs )

        super().__init__()
        self.ltmc = LayerTilesMapCanvas( layer )
        self.registerLayers = registerLayers
        self.id_layer = layer.id()
        self.name = f"tilemap_{registerLayers[self.id_layer]['id']:03d}"
        self.msgBar = QgsUtils.iface.messageBar()
        self.titleRemoveFiles = 'Remove images - {}'
        items = setGui()
        # Name
        html = f'<b style="background-color:LightGray;"><i>{self.name}</i></b>'
        items.lblName.setText( html )
        # Url
        frm_url = self.registerLayers[ self.id_layer ][ self.KEYPROPERTY_URL ]
        if frm_url:
            self.ltmc.format_url = frm_url
            items.leUrl.setValue( frm_url )
            self.ltmc.setLayerName()
        else:
            items.leUrl.setValue( None )
            items.rbDownload.setEnabled( False )
        # Directory
        items.wgtDir.setStorageMode( QgsFileWidget.GetDirectory )
        items.wgtDir.lineEdit().setNullValue('Select tiles directory')
        dirTiles = self.registerLayers[ self.id_layer ][ self.KEYPROPERTY_DIR ]
        total = 0
        if dirTiles and os.path.isdir( dirTiles ):
            items.wgtDir.lineEdit().setValue( dirTiles )
            total = self.ltmc.getTotalImages( dirTiles )
        else:
            items.wgtDir.lineEdit().setValue( None )
            items.rbDownload.setEnabled( False )
        text = self.titleRemoveFiles.format( total )
        items.btnRemoveFiles.setText( text )
        # Zoom and Total tiles
        index = items.cbZoom.findText( str( self.ltmc.zoom ) )
        if index > -1:
            items.cbZoom.setCurrentIndex( index )
            items.lblTiles.setText(f"{self.ltmc.totalTiles} Tiles")
        #
        items.rbUpdate.setChecked( True )
        items.ckVrt.setChecked( False )
        # Create self variables from items
        for idx in range( len( items._fields ) ):
            name = items._fields[ idx ]
            value = items[ idx ]
            if name == 'lblName':
                continue
            self.__dict__[ name ] = value

        # Connections
        self.ltmc.changeZoom.connect( self.on_changeZoom )
        self.ltmc.finishProcess.connect( self.on_finishProcess)
        self.btnOk.clicked.connect( self.on_clickedOk )
        self.btnRemoveFiles.clicked.connect( self.on_clickedRemoved )
        self.cbZoom.currentTextChanged.connect( self.on_currentTextChanged )
        self.wgtDir.fileChanged.connect( self.on_fileChanged )

    @pyqtSlot(int, int)
    def on_changeZoom(self, zoom, totalTiles):
        index = self.cbZoom.findText( str( zoom ) )
        if index == -1:
            self.msgBar.clearWidgets()
            args = (
                self.ltmc.__class__.__name__,
                f"Zoom {zoom} of map is outside range",
                Qgis.Warning, 2
            )
            self.msgBar.clearWidgets()
            self.msgBar.pushMessage( *args )
            return
        self.cbZoom.setCurrentIndex( index )
        self.lblTiles.setText(f"{self.ltmc.totalTiles} Tiles")

    @pyqtSlot(dict)
    def on_finishProcess(self, data):
        # name: update, download, count_images, remove_images
        def getTotal(text):
            value = text.replace('&', '')
            ini = value.index('-')
            return int( value[ini+1:] )

        def updateProperties(name):
            k_v = {
                'update': {
                    'key': self.KEYPROPERTY_URL,
                    'value': self.ltmc.format_url
                },
                'download': {
                    'key': self.KEYPROPERTY_DIR,
                    'value': self.wgtDir.lineEdit().value().replace('\n','')
                }
            }
            key = k_v[name]['key']
            value = k_v[name]['value']
            self.registerLayers[ self.id_layer ][ key ] = value
            self.ltmc.setCustomProperty( key, value )

        def pushMessage():
            msg = f"{data['name'].capitalize().replace('_', ' ')}"
            args = ( self.__class__.__name__ , ) # Tuple ','
            self.btnOk.setText('OK')
            if 'error' in data:
                msg = f"{msg}: {data['error']}"
                args += ( msg, Qgis.Critical, 4 )
            elif data['canceled']:
                msg = f"{msg}: Canceled by user"
                if 'total' in data:
                    msg = f"{msg} ({data['total']} total)"
                args += ( msg, Qgis.Warning, 4 )
            else:
                msg = f"{msg}: Finished"
                if 'total' in data:
                    msg = f"{msg} ({data['total']} total)"
                args += ( msg, Qgis.Info, 2 )
            self.msgBar.pushMessage( *args )
        
        # Message
        if not data['name'] in ('count_images', 'update'):
            pushMessage()
        # btnOk
        if data['name'] in ('update', 'download'):
            self.btnOk.setText('OK')
            if data['name'] == 'update':
                self.ltmc.setLayerName()
            else:
                self.rbUpdate.setChecked( True )
            updateProperties( data['name'] )

        # btnRemoveFiles
        if data['name'] in ('download', 'count_images', 'remove_images'):
            total = 0
            if data['name'] in ('download', 'count_images'):
                total = data['total']
            if data['name'] == 'download':
                total += getTotal( self.btnRemoveFiles.text() )
            text = self.titleRemoveFiles.format( total )
            self.btnRemoveFiles.setText( text )

    @pyqtSlot(bool)
    def on_clickedOk(self, checked):
        def update():
            def checkUrl():
                def pushMessage(msg):
                    args = (
                        self.ltmc.__class__.__name__,
                        msg,
                        Qgis.Warning, 8
                    )
                    self.msgBar.pushMessage( *args )

                if not bool( url ):
                    self.ltmc.format_url = url
                    return False
                try:
                    self.ltmc.format_url = url
                except Exception as e:
                    pushMessage( f"URL: {str(e)}" )
                    return False
                return True

            zoom = int( self.cbZoom.currentText() )
            self.ltmc.zoom = zoom
            enabled = True
            url = self.leUrl.value().replace('\n','')
            dirPath = self.wgtDir.lineEdit().value()
            enabledUrl = checkUrl() # Set self.ltmc.format_url
            enabledDir = bool( dirPath ) and os.path.isdir( dirPath )
            enabled =  enabledUrl and enabledDir
            self.rbDownload.setEnabled( enabled )
            self.ltmc.updateFeatures()

        def download():
            dirPath = self.wgtDir.lineEdit().value()
            if not bool( dirPath ) or not os.path.isdir( dirPath ):
                args = (
                    self.ltmc.__class__.__name__,
                    f"Invalid directory '{dirPath}'",
                    Qgis.Warning, 4
                )
                self.msgBar.pushMessage( *args )
                self.rbDownload.setEnabled( False )
                return
            hasVrt = self.ckVrt.isChecked()
            self.ltmc.downloadTiles( self.name, dirPath, hasVrt )
        
        self.btnOk.setText('CANCEL')
        process = { True: update, False: download }
        process[ self.rbUpdate.isChecked() ]()

    @pyqtSlot(bool)
    def on_clickedRemoved(self, checked):
        dirPath = self.wgtDir.lineEdit().value()
        if not bool( dirPath ) or not os.path.isdir( dirPath ):
            msg = 'Missing directory' if not bool( dirPath ) \
            else f"Not found '{dirPath}'"
            args = (
                self.ltmc.__class__.__name__,
                msg,
                Qgis.Warning, 4
            )
            self.msgBar.pushMessage( *args )
            return
        self.ltmc.removeImages( dirPath )
        self.btnRemoveFiles.setText('CANCEL')
    
    @pyqtSlot(str)
    def on_currentTextChanged(self, text):
        zoom = int( text )
        self.ltmc.zoom = zoom
        self.lblTiles.setText(f"{self.ltmc.totalTiles} Tiles")

    @pyqtSlot(str)
    def on_fileChanged(self, dirPath):
        valid = bool( dirPath ) and os.path.isdir( dirPath )
        self.rbDownload.setEnabled( valid )
        if valid:
            self.ltmc.getTotalImages( dirPath )


class LayerTilesMapCanvasWidgetProvider(QgsLayerTreeEmbeddedWidgetProvider):
    def __init__(self):
        super().__init__()
        self.layers = {} # Register properties of layer
        self.keys = (
            LayerTilesMapCanvasWidget.KEYPROPERTY_URL,
            LayerTilesMapCanvasWidget.KEYPROPERTY_DIR,
        )
        self.numRegister = 0

    def id(self):
        return self.__class__.__name__

    def name(self):
        return 'Layer Tiles'

    def createWidget(self, layer, widgetIndex):
        def addLayer(layer_id):
            self.layers[ layer_id ] = { 'id': self.numRegister }
            for key in self.keys:
                value = layer.customProperty( key, None )
                self.layers[ layer_id ][ key ] = value

        lid = layer.id()
        if not lid in self.layers:
            self.numRegister += 1
            addLayer( lid )
        return LayerTilesMapCanvasWidget( layer, self.layers )

    def supportsLayer(self, layer):
        return layer.customProperty( LayerTilesMapCanvasWidget.KEYPROPERTY_URL, -1 ) != -1


class LayerTilesMap(QObject):
    def __init__(self, iface):
        super().__init__()
        self.msgBar = iface.messageBar()
        self.project = QgsProject.instance()
        self.activeLayer = iface.activeLayer

    def register(self):
        self.widgetProvider = LayerTilesMapCanvasWidgetProvider()
        registry = QgsGui.layerTreeEmbeddedWidgetRegistry()
        if bool( registry.provider( self.widgetProvider.id() ) ):
            registry.removeProvider( self.widgetProvider.id() )
        registry.addProvider( self.widgetProvider )

    def addLayerRegisterProperty(self, layer):
        totalEW = int( layer.customProperty('embeddedWidgets/count', 0) )
        layer.setCustomProperty('embeddedWidgets/count', totalEW + 1 )
        layer.setCustomProperty(f"embeddedWidgets/{totalEW}/id", self.widgetProvider.id() )
        self.project.addMapLayer( layer )

    def run(self):
        def checkActiveLayer(ltmc):
            def getUrl():
                Info = collections.namedtuple('Info', 'url name')
                layer = self.activeLayer()
                if not bool(layer):
                    return Info(None, None)
                if layer is None or layer.providerType() != 'wms':
                    return Info(None, None)
                source = urllib.parse.unquote( layer.source() )
                if source.find('type=xyz') == -1:
                    return Info(None, None)
                idIni= source.find('url=') + 4
                idEnd = source.find('&zmax')
                return Info( source[idIni:idEnd], layer.name() )

            info = getUrl()
            if not bool( info.url ):
                ltmc.format_url = None
                return
            try:
                ltmc.format_url = info.url
            except Exception as e:
                self.msgBar.pushMessage(
                    self.__class__.__name__ ,
                    f"'{info.name}', URL: {str(e)}",
                    Qgis.Warning, 4
                )
                return
            layer.setCustomProperty( LayerTilesMapCanvasWidget.KEYPROPERTY_URL, info.url )
            
        if self.project.count() == 0:
            self.msgBar.pushMessage(
                self.__class__.__name__ ,
                'Missing layers',
                Qgis.Critical, 2
            )
            return

        layer = LayerTilesMapCanvas.createLayer()
        ltmc = LayerTilesMapCanvas( layer )
        checkActiveLayer( ltmc )
        ltmc.updateFeatures()
        ltmc = None
        self.addLayerRegisterProperty( layer )
