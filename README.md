# Sunsynk API and Analysis

> Builds on [sunsynk-api-client](https://pypi.org/project/sunsynk-api-client/) by James Ridgway (installed via pip), extended with energy cost analysis and battery simulation tools.

An API client library for reading data from the Sunsynk API that is used by the Sunsynk Connect apps and
[PowerView](https://pv.inteless.com/) portal, plus a CLI tool for analysing historical energy usage, costs, and solar/battery return on investment.


## Setup

```bash
bash setup.sh          # creates venv and installs sunsynk-api-client + dependencies
```

## Example Usage

    import asyncio
    import os
    
    from sunsynk.client import SunsynkClient
    
    
    async def main():
        sunsynk_username = os.getenv('SUNSYNK_USERNAME')
        sunsynk_password = os.getenv('SUNSYNK_PASSWORD')
    
        async with SunsynkClient(sunsynk_username, sunsynk_password) as client:
            inverters = await client.get_inverters()
            for inverter in inverters:
                grid = await client.get_inverter_realtime_grid(inverter.sn)
                battery = await client.get_inverter_realtime_battery(inverter.sn)
                solar_pv = await client.get_inverter_realtime_input(inverter.sn)
    
                await client.get_inverter_realtime_output(inverter.sn)
    
                print(f"Inverter (sn: {inverter.sn}) is drawing {grid.get_power()}kWh from the grid, {battery.power}kWh from battery and {solar_pv.get_power()}kWh.")
    
        print('Done!')
    
    asyncio.run(main())
