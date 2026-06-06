import datetime
import json
import re
import math
from enum import Enum

from sunsynk.resource import Resource
from sunsynk.calculations import VirtualBattery, QueryType
from datetime import datetime

class EnergyMonth(Resource):
    def __init__(self, data):
        self.Load = None
        self.data = data
        #print(data)
        self.energy = json.loads(json.dumps(self.data['infos']))
        #print(self.energy)
        for item in self.energy:
            #print(item['label'])
            if item['label'] == "Load" or item['label'] == "Load Power Consumption":
                self.Load = item
            elif item['label'] == "PV" or item['label'] == "PV Generation":
                self.PV = item  
            elif item['label'] == "Export" or item['label'] == "Sold electricity":
                self.Export = item  
            elif item['label'] == "Import" or item['label'] == "Purchased electricity":
                self.Import = item  
            #elif item['label'] == "Load":
                #self.Load = EnergySummary(item)  
                
    def get_Load(self):
        return self.Load
        
    def get_PV(self):
        return self.PV
        
    def get_Export(self):
        return self.Export
        
    def get_Import(self):
        return self.Import    

class EnergySummary(Resource):
    def __init__(self, data, month: EnergyMonth, battery: VirtualBattery, offpeakstart = "00:00", offpeakstop = "00:07", date = "", isLoad = 0):
        self.label = data['label']
        #print(f"Local label: {self.label}")
        self.records = data['records']
        self.peak = 0
        self.peakexport = 0
        self.offpeak = 0
        self.offpeakexport = 0
        self.offpeakpercentage = 0
        count = 0
        isOffPeak = 0
        
        mismatch = 0
        mismatch2 = 0
        
        start = datetime.strptime(offpeakstart, "%H:%M") 
        stop = datetime.strptime(offpeakstop, "%H:%M")
        
        hasBatteryRunOut = 0
        
        for record in self.records:
            time = datetime.strptime(record['time'], "%H:%M")
            value = float(record['value'])/12
            
            if time >= start:
                isOffPeak = 1
            if isOffPeak == 1:
                if time >= stop:
                    isOffPeak = 0
             
            if isOffPeak == 1:
            #if re.match("^0[0-6]:", record['time']):
                
                if count == 0:
                    count = 1
                    if isLoad == 1:
                        battery.recharge()
                    
                if value > 0:
                    self.offpeak = self.offpeak + value
                else:
                    
                    extra_load = min(value*-1,2.3)  #0.0023kW/5min power consumption of inverter
                    export = (value*-1) - extra_load
                    self.offpeakexport = self.offpeakexport + export #value*-1
                    self.offpeak = self.offpeak - extra_load
            else:
                
                if value > 0:
                    self.peak = self.peak + value
                    if isLoad == 1:
                        temp = battery.utilise(value, time)
                        if temp > 0 :
                            hasBatteryRunOut = 1
                else:
                    #self.peakexport = self.peakexport + value*-1
                    
                    extra_load = min(value*-1, 2.3)
                    export = (value*-1) - extra_load
                    self.peakexport = self.peakexport + export #value*-1
                    self.peak = self.peak - extra_load
                    if isLoad == 1:
                        #battery.PVCharge(value*-1, time)
                        battery.PVCharge(export, time)
        
        if hasBatteryRunOut == 1:
            battery.setRanOut()
         
        if self.offpeak + self.peak > 0:
            self.offpeakpercentage = self.offpeak / (self.offpeak + self.peak)
 
class EnergyDay(Resource):
    def __init__(self, data, date: str, month: EnergyMonth, battery: VirtualBattery, offpeakstart: str, offpeakstop: str):
        self.data = data
        energy = json.loads(json.dumps(self.data['infos']))
        for item in energy:
            #print(item['label'])
            if item['label'] == "PV":
                self.PV = EnergySummary(item, month, battery, offpeakstart, offpeakstop, date, 0)
                #print(f"PV: {self.PV.peakexport} + {self.PV.offpeakexport}")
            elif item['label'] == "Grid":
                self.Grid = EnergySummary(item, month, battery, offpeakstart, offpeakstop, date, 1)  
            #elif item['label'] == "Battery":
                #self.Battery = EnergySummary(item)  
            #elif item['label'] == "SOC":
                #self.SOC = EnergySummary(item)  
            elif item['label'] == "Load":
                self.Load = EnergySummary(item, month, battery, offpeakstart, offpeakstop, date, 0)  
        
        self.suppliedLoad = 0
        self.suppliedImport = 0
        self.suppliedExport = 0
        self.suppliedPV = 0
        
        items = month.get_Load()
        for day in items['records']:
            if day['time'] == date:
                self.suppliedLoad = float(day['value'])
                #print(f"Time: {date} Load: {day['value']}kwH")
                
                
        items = month.get_Import()
        for day in items['records']:
            if day['time'] == date:
                self.suppliedImport = float(day['value'])
                #print(f"Time: {date} Import: {day['value']}kwH")
                
        items = month.get_PV()
        for day in items['records']:
            if day['time'] == date:
                self.suppliedPV = float(day['value'])
                #print(f"Time: {date} PV: {day['value']}kwH")
                
        items = month.get_Export()
        for day in items['records']:
            if day['time'] == date:
                self.suppliedExport = float(day['value'])
                #print(f"Time: {date} Export: {day['value']}kwH")

    def getCalcExport(self, qtype: QueryType=QueryType.BOTH):
        if qtype == QueryType.BOTH:
            return self.Grid.peakexport + self.Grid.offpeakexport
        elif qtype == QueryType.PEAK:
            return self.Grid.peakexport
        elif qtype == QueryType.OFFPEAK:
            return self.Grid.offpeakexport
        else:
            return 0
          
        
    def getCalcImport(self, qtype: QueryType=QueryType.BOTH):
        if qtype == QueryType.BOTH:
            return self.Grid.peak + self.Grid.offpeak
        elif qtype == QueryType.PEAK:
            return self.Grid.peak
        elif qtype == QueryType.OFFPEAK:
            return self.Grid.offpeak
        else:
            return 0
        
    def getCalcExportPeak(self):
        return self.Grid.peakexport 
        
    def getCalcImportPeak(self):
        return self.Grid.peak 
        
    def getCalcExportOffPeak(self):
        return self.Grid.offpeakexport
        
    def getCalcImportOffPeak(self):
        return self.Grid.offpeak
        
    def getCalcPV(self):
        return self.PV.peak + self.PV.offpeak
        
    def getCalcPVPeak(self):
        return self.PV.peak 
        
    def getCalcPVOffPeak(self):
        return self.PV.offpeak
        
    def getCalcLoad(self):
        return self.Load.peak + self.Load.offpeak
        
    def getCalcLoadPeak(self):
        return self.Load.peak 
        
    def getCalcLoadOffPeak(self):
        return self.Load.offpeak
        
    def getSuppliedLoad(self):
        return self.suppliedLoad
        
    def getSuppliedExport(self):
        return self.suppliedExport
        
    def getSuppliedImport(self):
        return self.suppliedImport
        
    def getSuppliedPV(self):
        return self.suppliedPV
        
    def print(self):
        label = ":"
        if (self.getCalcExport(QueryType.BOTH)) > 0 :
            print(f"Export: {round((self.getCalcExport())/1000, 2)}kWh (vs {round(self.getSuppliedExport(), 1)}kWh), Diff = {round(self.getSuppliedExport() - self.getCalcExport()/1000,2)}kWh  %age {round(self.getSuppliedExport()/(self.getCalcExport()/1000),2)}")
            label = " Import: "
        print(f"Import {round((self.getCalcImport())/1000, 2)}kWh (vs {round(self.getSuppliedImport(), 1)}kWh), Diff = {round(self.getSuppliedImport() - self.getCalcImport()/1000,2)}kWh")
        print(f"PV {round((self.getCalcPV())/1000, 2)}kWh (vs {round(self.getSuppliedPV(), 1)}kWh), Diff = {round(self.getSuppliedPV() - self.getCalcPV()/1000,2)}kWh   %age {round(self.getSuppliedPV()/(self.getCalcPV())/1000,2)}")
        