# -*- coding: utf-8 -*-
"""
/***************************************************************************
Name                 : Layer tiles mapcanvas
Description          : Create a layer with grid of tiles from extent and zoom of map canvas.
Date                 : May, 2020
copyright            : (C) 2020 by Luiz Motta
email                : motta.luiz@gmail.com

 ***************************************************************************/

/***************************************************************************
 *                                                                         *
 *   This program is free software; you can redistribute it and/or modify  *
 *   it under the terms of the GNU General Public License as published by  *
 *   the Free Software Foundation; either version 2 of the License, or     *
 *   (at your option) any later version.                                   *
 *                                                                         *
 ***************************************************************************/
"""

__author__ = 'Luiz Motta'
__date__ = 'c'
__copyright__ = '(C) 2020, Luiz Motta'
__revision__ = '$Format:%H$'



import os, math
import collections
import urllib.request, urllib.error
import multiprocessing
from concurrent import futures
from functools import partial

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


def getResponseValue(url, timeout=None):
    ValueResponse = collections.namedtuple('ValueResponse', 'value error')
    try:
        args = {
            'url': url,
            'headers': { 'User-agent': 'QGIS Plugin' }
        }
        request = urllib.request.Request( **args )
        args = { 'url': request }
        if not timeout is None:
            args['timeout'] = timeout
        response = urllib.request.urlopen( **args )
    except ValueError as e:
        r = ValueResponse( None, str( e )  )
        response = None
        return r
    except urllib.error.HTTPError as e:
        msg =  str( e )
        content_type = e.headers.get_content_type().split('/')
        if content_type[0] == 'text' and content_type[1] != 'html':
            msg = e.read().decode("utf-8")
        r = ValueResponse( None, msg )
        response = None
        return r
    except urllib.error.URLError as e:
        r = ValueResponse( None, str( e )  )
        response = None
        return r
    r = ValueResponse( response.read(), None )
    response.close()
    return r


class TilesMapCanvas():
    MAXSCALEPERPIXEL = 156543.04
    INCHESPERMETER = 39.37
    CRS4326 = QgsCoordinateReferenceSystem('EPSG:4326')
    CRS3857 = getCRS_3857()
    FIELDS = 'x y z q rect'
    def __init__(self):
        self.mapCanvas = QgsUtils.iface.mapCanvas()
        self.ct =  QgsCoordinateTransform( self.CRS4326, self.CRS3857, QgsCoordinateTransformContext() )

    def _deg2num(self, vlong, vlat, zoom):
        # Adaptation from 'https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames'
        lat_rad = math.radians(vlat)
        n = 2.0 ** zoom
        xtile = int((vlong + 180.0) / 360.0 * n)
        ytile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
        return ( xtile, ytile )

    def _getQuadKey(self, zoom, xtile, ytile):
        # Adaptation from 'https://gist.github.com/maptiler' 
        quadKey = ""
        for i in range( zoom, 0, -1):
            digit = 0
            mask = 1 << (i-1)
            if (xtile & mask) != 0:
                digit += 1
            if (ytile & mask) != 0:
                digit += 2
            quadKey += str( digit )
        return quadKey

    def _getExtentTiles(self, zoom):
        def getExtentMapCanvas():
            mapSettings = self.mapCanvas.mapSettings()
            crsCanvas = mapSettings.destinationCrs()
            extent = self.mapCanvas.extent()
            if self.CRS4326 != crsCanvas:
                ct =  QgsCoordinateTransform( crsCanvas, self.CRS4326, QgsCoordinateTransformContext() )
                return ct.transform( extent )
            return extent

        extent = getExtentMapCanvas() # EPSG 4326
        tile_x1, tile_y1 = self._deg2num( extent.xMinimum(), extent.yMaximum(), zoom )
        tile_x2, tile_y2 = self._deg2num( extent.xMaximum(), extent.yMinimum(), zoom )
        ExtentTiles = collections.namedtuple('ExtentTiles', 'x1 y1 x2 y2')
        return ExtentTiles( tile_x1, tile_y1, tile_x2, tile_y2 )        

    def getTileCenter(self, zoom):
        mapSettings = self.mapCanvas.mapSettings()
        crsCanvas = mapSettings.destinationCrs()
        center = self.mapCanvas.center()
        if self.CRS4326 != crsCanvas:
            ct =  QgsCoordinateTransform( crsCanvas, self.CRS4326, QgsCoordinateTransformContext() )
            center = ct.transform( center )
        ( xtile, ytile ) = self._deg2num( center.x(), center.y(), zoom )
        qtile = self._getQuadKey( zoom, xtile, ytile )
        Tile = collections.namedtuple('Tile', self.FIELDS )
        return Tile( xtile, ytile, zoom, qtile, None )

    def total(self, zoom):
        e = self._getExtentTiles( zoom )
        return ( e.x2 - e.x1 + 1 ) * ( e.y2 - e.y1 + 1 )

    def __call__(self, zoom):
        def num2deg(xtile, ytile):
            # Adaptation from 'https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames'
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

        Tile = collections.namedtuple('Tile', self.FIELDS )
        e = self._getExtentTiles( zoom )
        for x in range( e.x1, e.x2+1):
            for y in range( e.y1, e.y2+1):
                quadKey = self._getQuadKey( zoom, x, y )
                tile = Tile( x, y, zoom, quadKey, getRectTile( x, y ) ) # EPSG 3857
                yield tile


class TaskDownloadTiles(QgsTask):
    image = pyqtSignal(dict)
    finish = pyqtSignal(dict)
    def __init__(self, name, zoom, totalFeatures, iterInfoFeatures, dirPath, hasVrt, slotData, slotFinished):
        super().__init__( self.__class__.__name__, QgsTask.CanCancel )
        self.name = name
        self.zoom = zoom
        self.iterInfoFeatures = iterInfoFeatures
        self.totalFeats = totalFeatures
        self.countFeats = None
        self.countDownload = None
        self.hasCancel = None
        self.hasVrt = hasVrt
        self.filepathImagesVrt = []
        self.dirPath = dirPath
        self.driveTif = gdal.GetDriverByName('GTiff')
        self.wkt3857 = TilesMapCanvas.CRS3857.toWkt()
        self.image.connect( slotData )
        self.finish.connect( slotFinished )

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

            def progressDownload():
                self.countFeats += 1
                value = self.countFeats / self.totalFeats * 100
                self.setProgress( value )

            progressDownload()
            filepath = os.path.join( self.dirPath, f"{info.name}.tif" )
            if os.path.isfile( filepath ):
                r = getDataSource( filepath )
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
            rv = getResponseValue( info.url )
            if rv.value is None:
                msg = f"{rv.error} {info.url}"
                self.image.emit( { 'name': info.name, 'message': msg } )
                return
            gdal.FileFromMemBuffer( memfile, rv.value )
            r = getDataSource( memfile )
            if not r['isOk']:
                gdal.Unlink(memfile)
                self.image.emit( { 'name': info.name, 'message': r['message'] } )
                return
            self.countDownload += 1
            ds = self.driveTif.CreateCopy( filepath, r['ds'] )
            r['ds'] = None
            gdal.Unlink( memfile )
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
        for info in self.iterInfoFeatures:
            downloadImage( info )
            if self.isCanceled():
                return None
        return True

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

        if self.hasVrt and bool( self.filepathImagesVrt ):
            createVrt()
            self.filepathImagesVrt.clear()
        self.finish.emit( { 'canceled': self.isCanceled(), 'total': self.countDownload } )


class LayerTilesMapCanvas(QObject):
    FIELDS = { 'x': 'integer', 'y': 'integer', 'z': 'integer', 'q': 'string(-1)' }
    changeZoom = pyqtSignal( int, int )
    finishProcess = pyqtSignal(dict) # { 'name', 'canceled', 'error', 'total' }
    messageLogError = pyqtSignal(str)
    def __init__(self, layer ):
        super().__init__()
        self._layer = layer
        self._frm_url, self.getUrl = None, None
        self._tilesCanvas = TilesMapCanvas()
        self.mapCanvas = QgsUtils.iface.mapCanvas()
        self.project = QgsProject.instance()
        self.taskManager = QgsApplication.taskManager()
        self._currentTask = None
        self.root = self.project.layerTreeRoot()
        self._nameGroupTiles = 'Tile images'
        self._ltgTiles = self.root.findGroup( self._nameGroupTiles )
        self._ltl = self.root.findLayer( layer )
        self._zoom = self._getZoom()
        self._connect()

    def _getZoom(self):
        # Adaptation from 'https://gis.stackexchange.com/questions/268890/get-current-zoom-level-from-qgis-map-canvas'
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
        self._ltgTiles = self.root.addGroup( self._nameGroupTiles )
        self._ltgTiles.setItemVisibilityChecked( False )

    def _removeLayersTile(self):
        ltgTiles = self.root.findGroup( self._nameGroupTiles )
        if ltgTiles:
            self.project.removeMapLayers( ltgTiles.findLayerIds() )
            self.mapCanvas.refresh()

    @property
    def visible(self): return self._ltl.isVisible()

    @property
    def zoom(self): return self._zoom

    @zoom.setter
    def zoom(self, value): self._zoom = value

    @property
    def format_url(self): return self._frm_url

    @format_url.setter
    def format_url(self, value):
        self._frm_url = None
        if not bool(value):
            return
        totalZXY = value.find('z') + value.find('x') + value.find('y')
        if totalZXY < 0:
             self.getUrl = lambda info: value.format( q=info.q )
        else:
            self.getUrl = lambda info: value.format(
                z=info.z, x=info.x, y=info.y
            )
        info = self._tilesCanvas.getTileCenter(3)
        url = self.getUrl( info )
        rv = getResponseValue( url, 5 )
        if rv.value is None:
            msg = f"{rv.error} {url}"
            raise Exception( msg )
        self._frm_url = value

    def setLayerName(self):
        name  = f"Tiles Z={self._zoom}"
        if self._frm_url:
            name += ' URL'
        self._layer.setName( name )

    def setCustomProperty(self, key, value ):
        self._layer.setCustomProperty(key, value )

    def getTotalTiles(self): return self._tilesCanvas.total( self._zoom  )

    @pyqtSlot(float)
    def on_scaleChanged(self, scale_):
        zoom = self._getZoom()
        if self._zoom != zoom:
            self._zoom = zoom
            self.changeZoom.emit( zoom, self._tilesCanvas.total( zoom ) )

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

        def run(task, prov):
            totalTiles = self.getTotalTiles()
            c_tiles = 0
            for tile in self._tilesCanvas( self._zoom ):
                if task.isCanceled():
                    return { 'canceled': True, 'total': c_tiles }
                feat = QgsFeature()
                geom = QgsGeometry.fromRect( tile.rect )
                feat.setGeometry( geom )
                feat.setAttributes( [ tile.x, tile.y, tile.z, tile.q ] )
                prov.addFeature( feat )
                progress = c_tiles / totalTiles * 100
                task.setProgress( progress )
                c_tiles += 1
            return { 'canceled': False, 'total': c_tiles }

        def finished(exception, result=None):
            self._layer.updateExtents()
            self._layer.triggerRepaint()
            self._currentTask = None
            r = { 'name': 'update' }
            if exception:
                r['error'] = f"Exception, {exception}"
            r.update( result )
            self.finishProcess.emit( r )

        if self._currentTask:
            self._currentTask.cancel()
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
            'on_finished': finished
        }
        self._currentTask = QgsTask.fromFunction( **args )
        self._currentTask.setDependentLayers( [ self._layer ] )
        self.taskManager.addTask( self._currentTask )

    @pyqtSlot(str) # download
    def downloadTiles(self, name, dirPath, hasVrt):
        def getInfoFeatures(nameTile):
            InfoTile = collections.namedtuple('InfoTile', 'z x y q')
            fields = 'name url width height ulX ulY'
            InfoFeature = collections.namedtuple('InfoFeature', fields )
            featIterator = self._layer.getFeatures()
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
                yield InfoFeature( *args )

        @pyqtSlot(dict)
        def add(dictFile):
            """
            dictFile{'name', 'filepath'}
            """
            if 'message' in dictFile:
                self.messageLogError.emit( dictFile['message'] )
                return

            if not self.root.findGroup( self._nameGroupTiles ):
                self._createGroupTiles()
            layer = QgsRasterLayer( dictFile['filepath'], dictFile['name'] )
            self.project.addMapLayer( layer, False )
            self._ltgTiles.addLayer( layer )

        @pyqtSlot(dict)
        def finished(result):
            self._currentTask = None
            r = { 'name': 'download' }
            r.update( result )
            self.finishProcess.emit( r )

        if self._currentTask:
            self._currentTask.cancel()
            return

        if not self.root.findGroup( self._nameGroupTiles ):
            self._createGroupTiles()
        self._removeLayersTile()
        self.finishedTask = False
        zoom = next( self._layer.getFeatures() )['z']
        iterInfoFeatures = getInfoFeatures( name )
        args = (
            name, zoom,
            self._layer.featureCount(), iterInfoFeatures,
            dirPath, hasVrt,
            add, finished
        )
        self._currentTask = TaskDownloadTiles( *args )
        self._currentTask.setDependentLayers( [ self._layer ] )
        self.taskManager.addTask( self._currentTask )

    @pyqtSlot(str) # count_images
    def getTotalImages(self, dirPath):
        def run(task, dirPath):
            total = 0
            for f in os.listdir( dirPath ):
                if f.endswith('tif'): total += 1
            return { 'canceled': False, 'total': total }

        def finished(exception, result=None):
            self._currentTask = None
            r = { 'name': 'count_images' }
            if exception:
                r['error'] = f"Exception, {exception}"
            r.update( result )
            self.finishProcess.emit( r )

        if self._currentTask:
            self._currentTask.cancel()
            return
        # Task
        self.finishedTask = False
        args = {
            'description': f"{self.__class__.__name__}.getTotalImages",
            'function': run,
            'dirPath': dirPath,
            'on_finished': finished
        }
        self._currentTask = QgsTask.fromFunction( **args )
        self.taskManager.addTask( self._currentTask )

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
            self._currentTask = None
            r = { 'name': 'remove_images' }
            if exception:
                r['error'] = f"Exception, {exception}"
            r.update( result )
            self.finishProcess.emit( r )

        if self._currentTask:
            self._currentTask.cancel()
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
        self._currentTask = QgsTask.fromFunction( **args )
        self.taskManager.addTask( self._currentTask )


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
                leUrl.setToolTip( self.tipUrl.format('?') )
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
            # ItemsGui will be used outside
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
        self.tipUrl = 'Tile server: {}'
        items = setGui()
        # Name
        html = f'<b style="background-color:LightGray;"><i>{self.name}</i></b>'
        items.lblName.setText( html )
        # Url
        frm_url = self.registerLayers[ self.id_layer ][ self.KEYPROPERTY_URL ]
        if frm_url:
            self.ltmc.format_url = frm_url
            items.leUrl.setValue( frm_url )
            items.leUrl.setToolTip( self.tipUrl.format( frm_url ) )
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
            items.lblTiles.setText(f"{self.ltmc. getTotalTiles()} Tiles")
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

        logMessage = QgsApplication.messageLog().logMessage
        messageLogError = partial( logMessage, tag='LayerTilesMapCanvas', level=Qgis.Warning )
        # Connections
        self.ltmc.changeZoom.connect( self.on_changeZoom )
        self.ltmc.finishProcess.connect( self.on_finishProcess)
        self.ltmc.messageLogError.connect( messageLogError )
        self.btnOk.clicked.connect( self.on_clickedOk )
        self.btnRemoveFiles.clicked.connect( self.on_clickedRemoved )
        self.cbZoom.currentTextChanged.connect( self.on_currentTextChanged )
        self.wgtDir.fileChanged.connect( self.on_fileChanged )

    @pyqtSlot(int, int)
    def on_changeZoom(self, zoom, totalTiles):
        index = self.cbZoom.findText( str( zoom ) )
        if index == -1:
            if not self.ltmc.visible:
                return
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
        self.lblTiles.setText(f"{totalTiles} Tiles")

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
                    self.leUrl.setToolTip( self.tipUrl.format('?') )
                    return False
                try:
                    self.ltmc.format_url = url
                except Exception as e:
                    pushMessage( str( e ) )
                    self.leUrl.setToolTip( self.tipUrl.format('?') )
                    return False
                self.leUrl.setToolTip( self.tipUrl.format( url ) )
                return True

            zoom = int( self.cbZoom.currentText() )
            self.ltmc.zoom = zoom
            enabledUrl = True
            url = self.leUrl.value().replace('\n','')
            if self.ltmc.format_url != url:
                enabledUrl = checkUrl()
            dirPath = self.wgtDir.lineEdit().value()
            enabledDir = bool( dirPath ) and os.path.isdir( dirPath )
            enabled =  enabledUrl and enabledDir
            self.rbDownload.setEnabled( enabled )
            self.ltmc.updateFeatures()

        def download():
            if not bool( self.ltmc.format_url):
                args = (
                    self.ltmc.__class__.__name__,
                    'Missing tile server URL',
                    Qgis.Warning, 4
                )
                self.msgBar.pushMessage( *args )
                self.btnOk.setText('OK')
                self.rbUpdate.setChecked( True )
                return
            dirPath = self.wgtDir.lineEdit().value()
            if not bool( dirPath ) or not os.path.isdir( dirPath ):
                args = (
                    self.ltmc.__class__.__name__,
                    f"Invalid directory '{dirPath}'",
                    Qgis.Warning, 4
                )
                self.msgBar.pushMessage( *args )
                self.rbDownload.setEnabled( False )
                self.btnOk.setText('OK')
                self.rbUpdate.setChecked( True )
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
        self.lblTiles.setText(f"{self.ltmc.getTotalTiles()} Tiles")

    @pyqtSlot(str)
    def on_fileChanged(self, dirPath):
        valid = bool( dirPath ) and os.path.isdir( dirPath )
        if valid:
            self.ltmc.getTotalImages( dirPath )
        self.rbDownload.setEnabled( valid )


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
                    f"'{info.name}'. {str(e)}",
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

        filepath = f"{os.path.splitext(__file__)[0]}.qml"
        layer = createMemoryLayer('tiles', LayerTilesMapCanvas.FIELDS, 'Polygon', TilesMapCanvas.CRS3857, filepath  )
        ltmc = LayerTilesMapCanvas( layer )
        checkActiveLayer( ltmc )
        ltmc.updateFeatures()
        ltmc = None
        self.addLayerRegisterProperty( layer )
