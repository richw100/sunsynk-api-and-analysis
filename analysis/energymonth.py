from sunsynk.resource import Resource


class EnergyMonth(Resource):
    def __init__(self, data):
        self.Load = None
        self.data = data
        self.energy = self.data['infos']
        for item in self.energy:
            if item['label'] == "Load" or item['label'] == "Load Power Consumption":
                self.Load = item
            elif item['label'] == "PV" or item['label'] == "PV Generation":
                self.PV = item
            elif item['label'] == "Export" or item['label'] == "Sold electricity":
                self.Export = item
            elif item['label'] == "Import" or item['label'] == "Purchased electricity":
                self.Import = item

    def get_load(self):
        return self.Load

    def get_pv(self):
        return self.PV

    def get_export(self):
        return self.Export

    def get_import(self):
        return self.Import
