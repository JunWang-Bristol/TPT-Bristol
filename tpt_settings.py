def init():
    ###################################################################
    #                HARDWARE CONFIGURATION VARIABLES                 #
    ###################################################################
    global teensyConnected
    global picoConnected
    global psuHighConnected
    global psuLowConnected
    global psusConnected

    teensyConnected = False
    picoConnected = False
    psuHighConnected = False
    psuLowConnected = False
    psusConnected = False

    global PSU_HIGH_max_voltage  # maximum PSU_HIGH voltage
    global PSU_LOW_max_voltage   # maximum PSU_LOW voltage
    PSU_HIGH_max_voltage = 400
    PSU_LOW_max_voltage = 400

    global HALF_BRIDGE_max_current  # confirm if this value is correct
    HALF_BRIDGE_max_current = 25  # A

    global MICROCONTROLLER_max_duty_cycle
    global MICROCONTROLLER_min_duty_cycle
    global MICROCONTROLLER_max_frequency
    global MICROCONTROLLER_min_frequency
    global MICROCONTROLLER_max_N
    global MICROCONTROLLER_min_N
    MICROCONTROLLER_max_duty_cycle = 0.8
    MICROCONTROLLER_min_duty_cycle = 0.2
    MICROCONTROLLER_max_frequency = 300000  # Hz
    MICROCONTROLLER_min_frequency = 1000    # Hz
    MICROCONTROLLER_max_N = 250
    MICROCONTROLLER_min_N = 1

    ###################################################################
    #              MEASUREMENT CONFIGURATION VARIABLES                #
    ###################################################################
    # These variables take into account the maximum oscilloscope input
    # divided by the probe attenuation
    global max_input_after_attenuation_A_mV
    global max_input_after_attenuation_B_mV
    global max_input_after_attenuation_C_mA

    # These variables take into account the configured parameters in the 
    # microcontroller memory
    global T1_ns
    global T2A_ns
    global T2B_ns
    global T3_ns
    global N_cycles
    global S

    ###################################################################
    #                  MAGNETIC COMPONENT VARIABLES                   #
    ###################################################################
    global inductance_large_signal_uH  # medium/large signal inductance measured with the testbench
    global inductance_apriori_uH  # small-signal inductance measured with LCR (e.g. Bode100)
    
    global bode_gain  # two-dimensional array with (frequency, gain) data specified in absolute values
    global bode_gain_dB  # two-dimensional array with (frequency, gain) data specified in dB
    global bode_phase  # two-dimensional array with (frequency, phase) data specified in DEGREES
    global saturation_calculated
    global saturation_current
    global saturation_average_voltage
    global saturation_time_ns
    global saturation_calculated
    saturation_calculated = False

    global N1  # constructive parameter
    global N2  # constructive parameter
    
    global Ae  # effective area [m2]
    global le  # effective length [m]
    global Ve  # effective volume [m^3]

    ###################################################################
    #                          UX VARIABLES                           #
    ###################################################################

    global DPI
    DPI = 141.95107844318593

    global index_min_edge_TPT_plot
    global index_max_edge_TPT_plot

    global Actual_sample_time