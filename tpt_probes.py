###################################################################
#                      OSCILLOSCOPE PROBES                        #
###################################################################
# Definition of oscilloscope probes
probe_TA057_20x = {
    "ID": "V1",
    "Manufacturer": "Pico Technology",
    "PN": "TA057",
    "magnitude": "voltage",
    "available": True,
    "attenuation": 20,
    "units": "volt",
    "max_input_positive": 140,
    "max_input_negative": -140,
    "bandwidth_MHz": 25,
    "Description": "PicoTech TA057 - Attenuation ratio: 1/20"
}

probe_TA057_200x = {
    "ID": "V2",
    "Manufacturer": "Pico Technology",
    "PN": "TA057",
    "magnitude": "voltage",
    "available": True,
    "attenuation": 200,
    "units": "volt",
    "max_input_positive": 1400,
    "max_input_negative": -1400,
    "bandwidth_MHz": 25,
    "Description": "PicoTech TA057 - Attenuation ratio: 1/200"
}


probe_TA041_10x = {
    "ID": "V3",
    "Manufacturer": "Pico Technology",
    "PN": "TA041",
    "magnitude": "voltage",
    "available": False,
    "attenuation": 10,
    "units": "volt",
    "max_input_positive": 70,
    "max_input_negative": -70,
    "bandwidth_MHz": 25,
    "Description": "PicoTech TA057 - Attenuation ratio: 1/10"
}

probe_TA041_100x = {
    "ID": "V4",
    "Manufacturer": "Pico Technology",
    "PN": "TA041",
    "magnitude": "voltage",
    "available": False,
    "attenuation": 200,
    "units": "volt",
    "max_input_positive": 700,
    "max_input_negative": -700,
    "bandwidth_MHz": 25,
    "Description": "PicoTech TA057 - Attenuation ratio: 1/100"
}

probe_TA375_10x = {
    "ID": "V5",
    "Manufacturer": "Pico Technology",
    "PN": "TA375",
    "magnitude": "voltage",
    "available": False,
    "attenuation": 10,
    "units": "volt",
    "max_input_positive": 600,
    "max_input_negative": -600,
    "bandwidth_MHz": 100,
    "Description": "PicoTech passive probe TA375 - Attenuation ratio: 1/10"
}

probe_TA386_10x = {
    "ID": "V6",
    "Manufacturer": "Pico Technology",
    "PN": "TA386",
    "magnitude": "voltage",
    "available": True,
    "attenuation": 10,
    "units": "volt",
    "max_input_positive": 1400,
    "max_input_negative": -1400,
    "bandwidth_MHz": 200,
    "Description": "PicoTech TA386 - Attenuation ratio: 1/10"
}

probe_TCP2020 = {
    "ID": "C1",
    "Manufacturer": "Tektronix",
    "PN": "TCP2020",
    "magnitude": "current",
    "available": True,
    "attenuation": 10,  # 10 mA/mV = 10 A/V
    "units": "amp",
    "max_input_current_rms": 20,
    "max_input_current_peak": 100,
    "bandwidth_MHz": 50,
    "Description": "Tektronix TCP2020"
}

probe_N2783B = {
    "ID": "C2",
    "Manufacturer": "Keysight",
    "PN": "N2783B",
    "magnitude": "current",
    "available": False,
    "attenuation": 10,   # 10 mA/mV = 10 A/V
    "units": "amp",
    "max_input_current_rms": 30,
    "max_input_current_peak": 50,
    "bandwidth_MHz": 100,
    "Description": "Keysight N2783B"
}

list_voltage_probes = [probe_TA057_20x, probe_TA057_200x, probe_TA041_10x, probe_TA041_100x, probe_TA375_10x,probe_TA386_10x]
list_current_probes = [probe_TCP2020, probe_N2783B]


def get_probe_ID_largest_parameter(probe_type, parameter):
    # Returns the ID of the available probe with the highest parameter (i.e. max_input_current_rms)
    # Input arguments: probe_type ("voltage" / "current"), parameter (string)
    probe_ID = 0
    list_probes = []
    if (probe_type == "voltage"):
        list_probes = list_voltage_probes
    elif (probe_type == "current"):
        list_probes = list_current_probes

    if ((probe_type == "voltage") or (probe_type == "current")):    #otherwise return probe_ID = 0
        if (parameter in list_probes[0]):    #check if the parameter (key) exists in the list (first dictionary)
            if (type(list_probes[0][parameter]) == int or float):    #Check if it is a number
                highest_value_so_far = 0
                for i in range(0, len(list_probes)):
                    if (list_probes[i]["available"] and (list_probes[i][parameter] > highest_value_so_far)):
                        highest_value_so_far = list_probes[i][parameter]
                        probe_ID = list_probes[i]["ID"]
    
    return probe_ID


def get_probe_ID_enough_for(probe_type, parameter, value):
    # returns the ID of the probe with the smallest "parameter" that is
    # bigger or equal than "value"
    probe_ID = 0
    list_probes = []
    if (probe_type == "voltage"):
        list_probes = list_voltage_probes
    elif (probe_type == "current"):
        list_probes = list_current_probes

    if ((probe_type == "voltage") or (probe_type == "current")):    # otherwise return probe_ID = 0
        if (parameter in list_probes[0]):    # check if the parameter (key) exists in the list (first dictionary)
            if (type(list_probes[0][parameter]) == int or float):    # Check if it is a number
                # Build two lists of valid probes: one with the IDs, and the other with the values
                valid_values = []
                valid_IDs = []
                for i in range(0, len(list_probes)):
                    if (list_probes[i]["available"] and (list_probes[i][parameter] >= value)):
                        valid_IDs.append(list_probes[i]["ID"])
                        valid_values.append(list_probes[i][parameter])
                # get the ID at the index of the smallest value
                probe_ID = valid_IDs[valid_values.index(min(valid_values))]

    return probe_ID


def get_probe_dict_from_ID(probe_ID):
    # gets the dictionary with all the probes that measure
    # the same variable as probe_ID
    list_probes = []
    dict_probe = []
    if (probe_ID[0] == "V"):  # voltage probe
        list_probes = list_voltage_probes
    elif (probe_ID[0] == "C"):  # current probe
        list_probes = list_current_probes
    if(probe_ID[0] == "V" or "C"):
        for i in range(0, len(list_probes)):
            if (list_probes[i]["ID"] == probe_ID):
                dict_probe = list_probes[i]
    return dict_probe


def get_probe_from_ID(probe_ID):
    # gets the probe (the dictionary element) with probe_ID
    list_probes = []
    requested_probe = []

    if (probe_ID[0] == "V"):  # voltage probe
        list_probes = list_voltage_probes
    elif (probe_ID[0] == "C"):  # current probe
        list_probes = list_current_probes
    if(probe_ID[0] == "V" or "C"):
        for i in range(0, len(list_probes)):
            if (list_probes[i]["ID"] == probe_ID):
                requested_probe = list_probes[i]
                break
    return requested_probe
    