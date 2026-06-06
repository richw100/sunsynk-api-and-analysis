import asyncio
import os
import json
import sys
import re

from sunsynk.client import SunsynkClient

from sunsynk.calculations import VirtualBattery
from sunsynk.calculations import PriceData, EnergyPrices

from datetime import datetime

async def main():
    sunsynk_username = os.getenv('SUNSYNK_USERNAME')
    sunsynk_password = os.getenv('SUNSYNK_PASSWORD')

    print(f"collectdata.py showDays[ON|OFF] batterySizeW  usePVToChargeBattery[ON|OFF] startChargeW stopChargeW [_EnergyPrices.json|DEFAULT] startDate[YYYY-MM-DD] stopDate[YYYY-MM-DD]")
    
    print(f"Username: {sunsynk_username}")

    async with SunsynkClient(sunsynk_username, sunsynk_password, "https://api.sunsynk.net", "sunsynk") as client:
        inverters = await client.get_inverters()
        for inverter in inverters:
            grid = await client.get_inverter_realtime_grid(inverter.sn)
            battery = await client.get_inverter_realtime_battery(inverter.sn)
            solar_pv = await client.get_inverter_realtime_input(inverter.sn)

            await client.get_inverter_realtime_output(inverter.sn)

            showDays = 1
            if len(sys.argv) > 1 and re.match("^off", sys.argv[1], re.IGNORECASE): 
                showDays = 0
            
            batterySize = 5000
            if len(sys.argv) > 2: 
                batterySize = int(sys.argv[2])
                
            usePV = 0
            if len(sys.argv) > 3 and re.match("^on", sys.argv[3], re.IGNORECASE): 
                usePV = 1
            
            startCharge = 1000
            if len(sys.argv) > 4: 
                startCharge = int(sys.argv[4])

            stopCharge = 2000
            if len(sys.argv) > 5: 
                stopCharge = int(sys.argv[5])
                
            processDate = 1
            startDate = ""
            stopDate = ""
            
            if len(sys.argv) > 7:
                processDate = 0
                startDate = sys.argv[7]

            if len(sys.argv) > 8:
                stopDate = sys.argv[8]
                
            energyPricesJson = "_EnergyPrices.json"
            if len(sys.argv) > 6:
                energyPricesJson = sys.argv[6]
            if energyPricesJson == "DEFAULT":
                energyPricesJson = "_EnergyPrices.json"
            pricesFilename = "inverterData/" + energyPricesJson
            
            tmpPrice = PriceData()
            battery = VirtualBattery( tmpPrice, batterySize, usePV, startCharge, stopCharge)

            energyPricesJson = None

            try:
                with open(pricesFilename) as data_file:
                    print(f"Loading: {pricesFilename}")
                    energyPricesJson = json.load(data_file)
                    #print(f'Load from file: {req_type}-{date}.json')
            except Exception as e:
                print(e)
                exit(-1)
                
            prices = EnergyPrices(energyPricesJson, battery)

            days = 0
            hasyear = 1
            yearcount = 2025
            while yearcount < 2040:
                if hasyear == 1:
                    hasyear = 0
                    count = 1
                    while count < 13:
                        
                        join = "-"
                        if count < 10:
                            join = "-0"
                        monthtocheck = str(yearcount) + join + str(count)
                        energymonth = await client.get_energy_month(inverter.plant.id, monthtocheck)
                        
                        #print(energymonth)
                        
                        items = energymonth.get_Load()
                        
                        if items != None:
                            
                            for day in items['records']:
                                hasyear = 1
                                prices.checkDate(day['time'])
                                
                                checkDate = datetime.strptime(day['time'], "%Y-%m-%d")
                                if len(startDate): #and re.match(startDate, day['time']):
                                    startDateTime = datetime.strptime(startDate, "%Y-%m-%d")
                                    if checkDate > startDateTime:
                                        processDate = 1
                                        
                                if len(stopDate): #and re.match(stopDate, day['time']):
                                    stopDateTime = datetime.strptime(stopDate, "%Y-%m-%d")
                                    if checkDate > stopDateTime:
                                        processDate = 0
                                        

                                if processDate == 1:
                                    days += 1

                                    if showDays == 1:
                                        print(f"Calculating: {day['time']}")
                                    
                                    energyday = await client.get_energy_day(inverter.plant.id, day['time'], energymonth, prices.battery, prices.priceData.currentOffPeakStart, prices.priceData.currentOffPeakStop)
                                    prices.addData(energyday)
                                    
                                    if showDays == 1:
                                        energyday.print()
                                        #print(f"TotalCalcImport: {round(totalCalcImportPeak/1000, 2)}kWh, Battery: {battery.getChargeAmountkWh()}kWh.")

                        count += 1
                yearcount += 1
            
            totals = prices.get_grand_totals()
            
            prices.print_energy_summary()
           
            prices.print_averages()
            prices.print_totals()
            prices.print_return_on_investment()
            prices.print_savings()
            prices.print_battery()
            prices.print_costs()
    
asyncio.run(main())