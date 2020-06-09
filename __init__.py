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
__date__ = '2020-05-01'
__copyright__ = '(C) 2020, Luiz Motta'
__revision__ = '$Format:%H$'


import os

from qgis.PyQt.QtCore import QObject, pyqtSlot
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .layertilesmapcanvas import LayerTilesMap

def classFactory(iface):
  return LayerTilesMapcanvasPlugin( iface )

class LayerTilesMapcanvasPlugin(QObject):
    def __init__(self, iface):
        super().__init__()
        self.iface = iface
        self.namePlugin = u"Layer Tiles Mapcanvas"
        self.action = None

        self.ltm = LayerTilesMap( iface )

    def initGui(self):
        about = "Create grid tile with mapcanvas extent"
        icon = QIcon( os.path.join( os.path.dirname(__file__), 'layertilesmapcanvas.png' ) )
        self.action = QAction( icon, self.namePlugin, self.iface.mainWindow() )
        self.action.setObjectName( self.namePlugin.replace(' ', '') )
        self.action.setWhatsThis( about )
        self.action.setStatusTip( about )
        self.action.triggered.connect( self.run )

        self.iface.addWebToolBarIcon( self.action )
        self.iface.addPluginToWebMenu( self.namePlugin, self.action )
        self.ltm.register()

    def unload(self):
        self.iface.removeRasterToolBarIcon( self.action )
        self.iface.removePluginRasterMenu( self.namePlugin, self.action )
        del self.action

    @pyqtSlot(bool)
    def run(self, checked):
        self.ltm.run()
