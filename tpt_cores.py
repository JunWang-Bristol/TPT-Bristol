from tabulate import tabulate

core_0 = {
    "ID": "ETD59/31/22-2-3C94-H150",
    "N1": 13,
    "N2": 13.2,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 0.000368,  # m2
    "le": 0.139,  # m
    "Ve": 51500E-9  # m3
}

core_1 = {
    "ID": "T25/15/10 (N1=N2=4)",
    "N1": 4,
    "N2": 3.904,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 0.0000489,  # m2
    "le": 0.0602,  # m
    "Ve": 2944.0E-9  # m3
}

core_2 = {
    "ID": "ETD59/31/22-3-3C94-H150",
    "N1": 2,
    "N2": 2,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 0.000368,  # m2
    "le": 0.139,  # m
    "Ve": 51500E-9   # m3
}

core_3 = {
    "ID": "T25/15/10 (N1=N2=7)",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 0.0000489,  # m2
    "le": 0.0602,  # m
    "Ve": 2944.0E-9  # m3
}

core_4 = {
    "ID": "TX22/14/13-3C90",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 51.0E-6,  # m2
    "le": 0.054,  # m
    "Ve": 2750.0E-9  # m3
}

core_5 = {
    "ID": "TX25/15/13-3C90",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 62.1E-6,  # m2
    "le": 60.1E-3,  # m
    "Ve": 3740.0E-9  # m3
}

core_6 = {
    "ID": "TX26/15/20-3C90",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 112.0E-6,  # m2
    "le": 60.1E-3,  # m
    "Ve": 6720.0E-9  # m3
}

core_7 = {
    "ID": "TX42/26/18-3C90",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 134.0E-6,  # m2
    "le": 103.0E-3,  # m
    "Ve": 13810.1E-9  # m3
}

core_8 = {
    "ID": "TX51/32/19-3C90",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 172.0E-6,  # m2
    "le": 125.0E-3,  # m
    "Ve": 21500.0E-9  # m3
}

core_9 = {
    "ID": "TX80/40/15-3C90",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 288.0E-6,  # m2
    "le": 174.0E-3,  # m
    "Ve": 50200.0E-9  # m3
}

core_10 = {
    "ID": "PQ20/20-3C97",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 63.0E-6,  # m2
    "le": 46.0E-3,  # m
    "Ve": 2850.0E-9  # m3
}

core_11 = {
    "ID": "PQ35/35-3C97",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 190.0E-6,  # m2
    "le": 86.0E-3,  # m
    "Ve": 16300.0E-9  # m3
}

core_12 = {
    "ID": "PQ40/40-3C97",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 201.0E-6,  # m2
    "le": 102.0E-3,  # m
    "Ve": 20500.0E-9  # m3
}

core_13 = {
    "ID": "PQ50/50-3C97",
    "N1": 7,
    "N2": 7,  # measured using Bode100 (100kHz, -13 dBm)
    "Ae": 328.0E-6,  # m2
    "le": 113.0E-3,  # m
    "Ve": 37100.0E-9  # m3
}

core_14 = {
    "ID": "Blue core N87 - UoB Test version",
    "N1": 9,
    "N2": 9,
    "Ae": 267.2E-6,  # m2
    "le": 255.3E-3,  # m
    "Ve": 68200.0E-9  # m3
}

list_cores = [core_0, core_1, core_2, core_3, core_4, core_5, core_6, core_7, core_8, core_9,
    core_10, core_11, core_12, core_13, core_14]


def print_available_cores():    
    n_cores = len(list_cores)
    table_cores = []
    for i in range(0, n_cores):
        table_cores.append([i, list_cores[i]["ID"], list_cores[i]["N1"], list_cores[i]["N2"], list_cores[i]["le"], list_cores[i]["Ae"], list_cores[i]["Ve"]])
    print(tabulate(table_cores, headers=['Index', 'ID', 'N1', 'N2', 'le [m]', 'Ae [m2]', 'Ve [m3]'],  tablefmt='psql'))


def get_core(n):
    n_cores = len(list_cores)
    core = []
    if(n >= 0 and n < n_cores):
        core = list_cores[n]
    return core