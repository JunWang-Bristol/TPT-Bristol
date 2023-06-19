# file with functions that perform automated actions on the testbench
import tpt_PSU
import tpt_micro
import tpt_osc
import tpt_probes
import tpt_aux
import tpt_settings
import tpt_ser
import tpt_cores
import tpt_calculations
import tpt_group_variables
import draggable_lines_class


import serial  # pySerial, not Serial (important)
import time
import os
from scipy import stats
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import ctypes
from tabulate import tabulate
import math

from picosdk.functions import adc2mV, mV2adc, assert_pico_ok
from picosdk.constants import PICO_STATUS, PICO_STATUS_LOOKUP


def TPT_guided_test(chandle, pico_series):
    test_results = []
    clear_terminal()
    print("+---------------------------------------------+")
    print("|               Guided TPT test               |")
    print("+---------------------------------------------+")
    print("")

    debug = True
    if(debug):
        tpt_settings.inductance_large_signal_uH = 264
        L_uH = tpt_settings.inductance_large_signal_uH
        tpt_settings.saturation_current = 3
    else:
        L_uH = tpt_settings.inductance_large_signal_uH
        print("Large signal inductance:", round(L_uH, 3), "uH")
        print("Saturation current:", round(tpt_settings.saturation_current, 3), "A")

    print("\nList of available cores:")
    #print cores
    tpt_cores.print_available_cores()
    core_listed = tpt_aux.prompt_question_yes_no("Is the core under test shown in the list?", "Y")
    if(core_listed):
        index_core = int(input("\nPlease indicate the index of the core under test: "))
        core = tpt_cores.get_core(index_core)
        tpt_settings.N1 = core["N1"]
        tpt_settings.N2 = core["N2"]
        tpt_settings.le = core["le"]
        tpt_settings.Ae = core["Ae"]
        tpt_settings.Ve = core["Ve"]
        turns_ratio = tpt_settings.N2 / tpt_settings.N1
    else:
        turns_ratio = float(input("\nPlease define the turns ratio (or the V2/V1 gain) of the transformer: "))
        tpt_settings.le = float(input("\nPlease define the effective length (le [m]) of the transformer: "))
        tpt_settings.Ae = float(input("Please define the effective area (Ae [m2]) of the transformer: "))
        tpt_settings.Ve = float(input("Please define the effective volume (Ve [m3]) of the transformer: "))
        print("Consider adding the core data to the file \"tpt_cores.py\"")
    # TODO in future iterations this will take the open-circuit gain of the transformer (Bode100)

    Group_or_Single_input = tpt_aux.prompt_question_yes_no("Do you want to load Group variables" , "Y")
    if (Group_or_Single_input):

        Group_or_Single_count = 0
        print ("length of array" , len(tpt_group_variables.Group_data))
        #demagnetization_confirm = tpt_aux.prompt_question_yes_no("Do you want to apply demagnetization process (Slower but more accurate)", "Y")

        I_delta = tpt_group_variables.Group_data[0][0]
        f_Hz = tpt_group_variables.Group_data[0][1] * 1000
        I_0 = tpt_group_variables.Group_data[0][2]
        S = 0
        N_cycles = 4
        D = 0.5

        V_PSU_HIGH, V_PSU_LOW, V_pos, V_neg, T1_ns, T2A_ns, T2B_ns, T3_ns = \
            TPT_test_parameters_to_setup_parameters(L_uH, I_0, I_delta, D, f_Hz)
        parameters_OK = TPT_check_parameters(L_uH, tpt_settings.saturation_current, I_0, I_delta, D, f_Hz, V_PSU_HIGH,
                                             V_PSU_LOW, T1_ns, T2A_ns, T2B_ns, T3_ns, N_cycles)
        print("")
    else:
        print("Please enter the desired test parameters manually")
        I_0 = float(input("DC bias current [A]: "))
        if(I_0 >= 0):
            S = 0
        else:
            S = 1
        print("S (current sign): ", S)
        I_delta = float(input("Amplitude of current swing (half of total swing) [A]: "))
        f_Hz = float(input("Frequency [kHz]: ")) * 1000
        D = float(input("Duty cycle [0, 1]: "))
        N_cycles = float(input("Number of TPT cycles (Stage II): "))
        #demagnetization_confirm = tpt_aux.prompt_question_yes_no(
        #    "Do you want to apply demagnetization process (Slower but more accurate)", "Y")
        print("\nChecking if parameters are within range...")
        print("")
        V_PSU_HIGH, V_PSU_LOW, V_pos, V_neg, T1_ns, T2A_ns, T2B_ns, T3_ns = \
            TPT_test_parameters_to_setup_parameters(L_uH, I_0, I_delta, D, f_Hz)
        parameters_OK = TPT_check_parameters(L_uH, tpt_settings.saturation_current, I_0, I_delta, D, f_Hz, V_PSU_HIGH, V_PSU_LOW, T1_ns, T2A_ns, T2B_ns, T3_ns, N_cycles)
        print("")

    if parameters_OK:
        # Folder and date strings
        str_datetime = tpt_aux.get_datetime_string()
        str_folder = str_datetime + " - Test TPT"
        str_path = "Outputs/" + str_folder
        os.makedirs(str_path)

        # Store parameters in global variables
        tpt_settings.T1_ns = T1_ns
        tpt_settings.T2A_ns = T2A_ns
        tpt_settings.T2B_ns = T2B_ns
        tpt_settings.T3_ns = T3_ns
        tpt_settings.N_cycles = N_cycles
        tpt_settings.S = S
            
        # Definition of oscilloscope probes
        max_voltage_primary = max(V_PSU_HIGH, V_PSU_LOW)
        max_voltage_secondary = max_voltage_primary * turns_ratio
        max_current_primary = abs(I_0) + abs(I_delta)

        current_probe_ID = tpt_probes.get_probe_ID_enough_for("current", "max_input_current_peak", max_current_primary * 1.15)
        voltage_probe_primary_ID = tpt_probes.get_probe_ID_enough_for("voltage", "max_input_positive", max_voltage_primary * 1.15)
        voltage_probe_secondary_ID = tpt_probes.get_probe_ID_enough_for("voltage", "max_input_positive", max_voltage_secondary * 1.15)
        voltage_probe_primary_ID = "V6"
        voltage_probe_secondary_ID = "V3"
        current_probe_ID = "C2"
        print("--------------------------------------")
        print("\nVerify that the following probes are connected to the oscilloscope:")
        print("")
        print("- CHANNEL 1 -")
        print("Connect CH1 to the primary winding of the inductor")
        voltage_probe_primary = tpt_probes.get_probe_from_ID(voltage_probe_primary_ID)
        print(voltage_probe_primary["Description"])
        print("\n- CHANNEL 2 -")
        print("Connect CH2 to the secondary/auxiliary winding of the inductor")
        voltage_probe_secondary = tpt_probes.get_probe_from_ID(voltage_probe_secondary_ID)
        print(voltage_probe_secondary["Description"])
        print("\n- CHANNEL 3 -")
        current_probe = tpt_probes.get_probe_from_ID(current_probe_ID)
        print(current_probe["Description"])
        print("\n- CHANNEL 4 -")
        print("Unused.")
        print("")
        print("--------------------------------------")
        input("\nPress any key once the specified probes are in place.")
        print("\n--------------------------------------")

        continue_TPT_test = True
        n_iteration = 0
        V_difference_index = 0

        while (continue_TPT_test):
            print("\n - Iteration ", n_iteration, " -\n")

            if(tpt_settings.psusConnected):
                print("\nSetting PSU HIGH to", V_PSU_HIGH, " V...")
                tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_HIGH, V_PSU_HIGH)
                print("Setting PSU LOW to", V_PSU_LOW, " V...")
                tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_LOW, V_PSU_LOW)
                tpt_PSU.enable_PSU(tpt_ser.ser_PSU_HIGH)
                tpt_PSU.enable_PSU(tpt_ser.ser_PSU_LOW)
                measured_V_PSU_HIGH = tpt_PSU.get_PSU_measured_voltage(tpt_ser.ser_PSU_HIGH)
                measured_V_PSU_LOW = tpt_PSU.get_PSU_measured_voltage(tpt_ser.ser_PSU_LOW)
                diff_rel_V_PSU_HIGH = 100 * abs((V_PSU_HIGH - measured_V_PSU_HIGH) / V_PSU_HIGH)
                diff_rel_V_PSU_LOW = 100 * abs((V_PSU_LOW - measured_V_PSU_LOW) / V_PSU_LOW)
                #print("Debug - diff_rel_V_PSU_HIGH:", diff_rel_V_PSU_HIGH)
                #print("Debug - diff_rel_V_PSU_LOW:", diff_rel_V_PSU_LOW)
                print("\nMeasured PSU_HIGH voltage:", measured_V_PSU_HIGH, "V (", "%s%%"%int(100 - diff_rel_V_PSU_HIGH), "of the reference)")
                print("Measured  PSU_LOW voltage:", measured_V_PSU_LOW, "V (", "%s%%"%int(100 - diff_rel_V_PSU_LOW), "of the reference)")
                while((diff_rel_V_PSU_HIGH > 5) or (diff_rel_V_PSU_LOW > 5)):
                    measured_V_PSU_HIGH = tpt_PSU.get_PSU_measured_voltage(tpt_ser.ser_PSU_HIGH)
                    measured_V_PSU_LOW = tpt_PSU.get_PSU_measured_voltage(tpt_ser.ser_PSU_LOW)
                    diff_rel_V_PSU_HIGH = 100 * abs((V_PSU_HIGH - measured_V_PSU_HIGH) / V_PSU_HIGH)
                    diff_rel_V_PSU_LOW = 100 * abs((V_PSU_LOW - measured_V_PSU_LOW) / V_PSU_LOW)
                    #print("Debug - diff_rel_V_PSU_HIGH:", diff_rel_V_PSU_HIGH)
                    #print("Debug - diff_rel_V_PSU_LOW:", diff_rel_V_PSU_LOW)
                    print("\nMeasured PSU_HIGH voltage:", measured_V_PSU_HIGH, "V (", "%s%%"%int(100 - diff_rel_V_PSU_HIGH), "of the reference)")
                    print("Measured PSU_LOW voltage:", measured_V_PSU_LOW, "V (", "%s%%"%int(100 - diff_rel_V_PSU_LOW), "of the reference)")
                print("--------------------------")
            else:
                print("Please set PSU HIGH to " + str(V_PSU_HIGH) + " volts.")
                print("Please set PSU LOW to " + str(V_PSU_LOW) + " volts.")
                input("\nPress any key once the PSUs are set.")

            # Store parameters in microcontroller memory
            print("\nStoring parameters in microcontroller memory...")
            # there is a little bug in the firmware, so we have to store one extra cycle (N_cycles + 1). Minor bug.
            tpt_micro.store_parameters_TPT(S, T1_ns, T2A_ns, T2B_ns, T3_ns, N_cycles + 1)
            # Print TPT data
            micro_table = [['S (sign)', S, " "],
                           ['T1', T1_ns, "ns"],
                           ['T2A', T2A_ns, "ns"],
                           ['T2B', T2B_ns, "ns"],
                           ['T3', T3_ns, "ns"],
                           ['N', N_cycles, " "]]
            print("Done. Stored parameters:")
            print(tabulate(micro_table, headers=['Variable', 'Value', 'Units'],  tablefmt='psql'))
            
            print("\nConfiguring oscilloscope...")
            print("- Vertical axis -")
            # Oscilloscope variables
            status = {}
            enabled = 1
            disabled = 0
            analogue_offset = 0.0

            # Set up oscilloscope channels
            # Set up channel A (PRIMARY VOLTAGE)
            max_input_voltage_A = 1.15 * max_voltage_primary / voltage_probe_primary["attenuation"]
            channel_range_A, _ = tpt_osc.get_oscilloscope_input_range(max_input_voltage_A, pico_series)
            tpt_osc.print_channel_range("A", channel_range_A, pico_series)
            channel_A_code = tpt_osc.oscilloscope_range_str2code("A", pico_series)
            tpt_osc.set_oscilloscope_channel_range(chandle, 
                                                   channel_A_code, 
                                                   channel_range_A,
                                                   analogue_offset,
                                                   pico_series)

            # Set up channel B (SECONDARY/AUXILIARY VOLTAGE)
            max_input_voltage_B = 1.15 * max_voltage_secondary / voltage_probe_secondary["attenuation"]
            channel_range_B, _ = tpt_osc.get_oscilloscope_input_range(max_input_voltage_B, pico_series)
            tpt_osc.print_channel_range("B", channel_range_B, pico_series)
            channel_B_code = tpt_osc.oscilloscope_range_str2code("B", pico_series)
            tpt_osc.set_oscilloscope_channel_range(chandle, 
                                                   channel_B_code, 
                                                   channel_range_B,
                                                   analogue_offset,
                                                   pico_series)

            # Set up channel C (PRIMARY CURRENT)
            # We know the maximum current that will be reached as long as the core does not
            # saturate. But since we are actually trying to saturate it, we need to leave 
            # some room for the sudden current increase. I suggest a 50% over the expected max current
            if(I_0 >= 0):
                expected_max_current = (T1_ns * 1e-9) * V_pos / (L_uH * 1e-6)
            else:
                expected_max_current = (T1_ns * 1e-9) * V_neg / (L_uH * 1e-6)

            max_input_voltage_C = 1.25 * expected_max_current / current_probe["attenuation"]  #conversion V-A
            channel_range_C, _ = tpt_osc.get_oscilloscope_input_range(max_input_voltage_C, pico_series)
            #channel_range_C = ps2.PS2000A_RANGE['PS2000A_1V']  #debug to acquire Teensy pulses, remove line
            tpt_osc.print_channel_range("C", channel_range_C, pico_series)
            channel_C_code = tpt_osc.oscilloscope_range_str2code("C", pico_series)
            tpt_osc.set_oscilloscope_channel_range(chandle, 
                                                   channel_C_code, 
                                                   channel_range_C,
                                                   analogue_offset,
                                                   pico_series)

            # Set up channel D (unused)
            channel_D_code = tpt_osc.oscilloscope_range_str2code("D", pico_series)
            tpt_osc.disable_oscilloscope_channel(chandle,
                                                 channel_D_code,
                                                 pico_series)

            # Trigger configuration

            # Finds the max ADC count, and max level per channel
            # handle = chandle
            # Value = ctype.byref(maxADC)
            maxADC = ctypes.c_int16()
            tpt_osc.check_oscilloscope_maximum_value(chandle, maxADC, pico_series)
            # bufferAMax = (ctypes.c_int16 * total_samples)()
            #from IPython import embed; embed()
            max_channel_A_mV = tpt_osc.adc2mV_single_value(maxADC.value, channel_range_A, maxADC, pico_series)
            max_channel_B_mV = tpt_osc.adc2mV_single_value(maxADC.value, channel_range_B, maxADC, pico_series)
            max_channel_C_mV = tpt_osc.adc2mV_single_value(maxADC.value, channel_range_C, maxADC, pico_series)
            # threshold level: roughly one-fifth of PSU voltage - specified for the oscilloscope in ADC counts
            if(I_0 >= 0):
                trigger_level = 0.2 * V_pos / voltage_probe_primary["attenuation"]  # in volts
            else:
                trigger_level = -0.2 * abs(V_neg) / voltage_probe_primary["attenuation"]  # in volts

            trigger_threshold_counts = mV2adc(1000 * trigger_level, channel_range_A, maxADC)
            print("Trigger level: ", mV2adc(1000 * trigger_level, channel_range_A, maxADC), " mV (", "%s%%"%int(round(100 * trigger_threshold_counts / maxADC.value, 0)), " over the full input range)")
            trigger_occurred = False
            #print("Debug - Trigger level: ", trigger_threshold_counts, "counts out of a maximum of ", maxADC.value)
            # channel_range_A               
            
            # Sets up single trigger
            if(I_0 >= 0):
                tpt_osc.set_oscilloscope_trigger(chandle, channel_A_code, trigger_threshold_counts, "RISING", pico_series)
            else:
                tpt_osc.set_oscilloscope_trigger(chandle, channel_A_code, trigger_threshold_counts, "FALLING", pico_series)

            print("- Horizontal axis -")
            total_TPT_time_ns = (T1_ns + T2B_ns) + (N_cycles - 1) * (T2A_ns + T2B_ns) + (T2A_ns + T3_ns) 
            total_TPT_time_ns = total_TPT_time_ns + math.floor(1.05 * (T2A_ns + T2B_ns))  # to add some time after the test

            # The code below is Pico 2405A-dependent; it should be encapsulated in tpt_osc.calculate_oscilloscope_time_config()
            desired_pre_trigger_samples = 100
            max_post_trigger_samples = 12000
            #max_post_trigger_samples = 16000  # roughly; for Pico 2405A - this should depend on pico_series... temporary fix
            # for 4-ch operation, limit of 16 ns sample time 
            desired_sample_time = max(4, math.floor(total_TPT_time_ns / max_post_trigger_samples))
            desired_post_trigger_samples = min(max_post_trigger_samples, math.ceil(1.05 * total_TPT_time_ns / desired_sample_time))
            #desired_sample_time = math.floor((T2A_ns + T2B_ns) / 2000)  # ns (a compromise)
            
            active_channels = 4  # obtain by code?
            #from IPython import embed; embed()
            # TODO CREO QUE ESTOY PERDIENDO MUCHOS SAMPLES DEL TPT POR LA PROPORCIÓN ENTRE SAMPLE TIME Y DESIRED_POST_TRIGGER_SAMPLES
            # REVISAR... DEBERÍA PRIORIZAR CUBRIR EL BUFFER AL MÁXIMO
            timebase_OK, n_timebase, actual_sample_time, actual_pre_trigger_samples, actual_post_trigger_samples \
                = tpt_osc.calculate_oscilloscope_time_config(chandle, desired_pre_trigger_samples, desired_post_trigger_samples, desired_sample_time, active_channels, pico_series)

            total_samples = actual_pre_trigger_samples + actual_post_trigger_samples
            tpt_settings.Actual_sample_time = int(actual_sample_time)



            print("Duration of TPT test: ", total_TPT_time_ns, " ns")
            print("Attempted sample time: ", desired_sample_time, " ns")
            print("Actual sample time: ", actual_sample_time, " ns (timebase(n): ", n_timebase, ")")
            # print("Timebase (n): ", n_timebase)
            print("Number of samples in TPT pulses: ", math.floor(total_TPT_time_ns / actual_sample_time))
            print("Total samples: ", total_samples)
            
            if(timebase_OK):
                print("Oscilloscope correctly configured.")
                tpt_osc.run_oscilloscope_block_acquisition(chandle, actual_pre_trigger_samples, actual_post_trigger_samples, n_timebase, pico_series)

                # Launch TPT test (Teensy)
                print("\nLaunching TPT test. Sending command to the microcontroller...")
                tpt_micro.execute_TPT_microcontroller()

                # Check for data collection to finish
                tpt_osc.wait_oscilloscope_acquisition_ready(chandle, pico_series)

                print("Retrieving data from the oscilloscope...")
                # Create buffers ready for assigning pointers for data collection
                bufferAMax = (ctypes.c_int16 * total_samples)()
                bufferBMax = (ctypes.c_int16 * total_samples)()
                bufferCMax = (ctypes.c_int16 * total_samples)()

                # Set data buffer location for data collection from channels A and C
                tpt_osc.set_data_buffers(chandle, channel_A_code, bufferAMax, total_samples, pico_series)
                tpt_osc.set_data_buffers(chandle, channel_B_code, bufferBMax, total_samples, pico_series)
                tpt_osc.set_data_buffers(chandle, channel_C_code, bufferCMax, total_samples, pico_series)
                
                # Create overflow location
                overflow = ctypes.c_int16()
                # create converted type totalSamples
                c_total_samples = ctypes.c_int32(total_samples)
                
                # Assign retrieved data from scope to buffers assigned above
                tpt_osc.get_oscilloscope_values(chandle, c_total_samples, overflow, pico_series)

                # convert ADC counts data to mV, and then to numpy array
                adc2mVChAMax = adc2mV(bufferAMax, channel_range_A, maxADC)
                adc2mVChBMax = adc2mV(bufferAMax, channel_range_B, maxADC)
                adc2mVChCMax = adc2mV(bufferCMax, channel_range_C, maxADC)
                A_array = np.array(adc2mVChAMax)  # Value in mV
                B_array = np.array(adc2mVChBMax)  # Value in mV
                C_array = np.array(adc2mVChCMax)  # Value in mV

                # convert to volt and reverse attenuation
                A_array = 0.001 * A_array * voltage_probe_primary["attenuation"]
                B_array = 0.001 * B_array * voltage_probe_secondary["attenuation"]
                C_array = 0.001 * C_array * current_probe["attenuation"]

                # Create time data (oscilloscope resolution)
                #time_array_ns = np.linspace(0, ((c_total_samples.value)-1) * timeIntervalns.value, c_total_samples.value)
                time_array_osc_ns = np.linspace(0, ((c_total_samples.value)-1) * actual_sample_time, c_total_samples.value)
                print("time_array_test", time_array_osc_ns)
                # Interpolate and correct skewing
                new_sample_time = 6  # ns
                skew_ns = 0.0
                interpolator = 1  # hold:0 / linear:1
                _, data_voltage_primary, data_time_ns = tpt_aux.interpolate_data(A_array, time_array_osc_ns, new_sample_time, 0.0, interpolator)
                _, data_voltage_secondary, _ = tpt_aux.interpolate_data(B_array, time_array_osc_ns, new_sample_time, 0.0, interpolator)
                _, data_current_primary, _ = tpt_aux.interpolate_data(C_array, time_array_osc_ns, new_sample_time, skew_ns, interpolator)
                print("Number of interpolated samples:", len(data_time_ns), "(new sample time:", new_sample_time, "ns)")

                # Create dataframe - check order with UoB
                df = pd.DataFrame(np.vstack((data_time_ns, data_voltage_primary, data_voltage_secondary, data_current_primary))).transpose()
                df.columns = ['Time', 'Vp', 'Vs', 'Cp']

                trigger_occurred = tpt_osc.determine_if_trigger_occurred(A_array, trigger_level)

                #FEEDBACK LOOP_1: Compare the voltage amplitude difference here
                difference = compare_voltage_level(T1_ns,T2A_ns,T2B_ns,N_cycles,new_sample_time,data_voltage_primary)
                print("Voltage difference is", difference, "V")
                V_difference_error = 0
                if (abs(difference) >= 0.15):
                    V_difference_error = 1
                    V_difference_index = V_difference_index+1
                    if (difference >= 0):
                        V_PSU_LOW = V_PSU_LOW - 0.1
                    else:
                        V_PSU_HIGH = V_PSU_HIGH + 0.1
                    if (V_difference_index == 1):
                        path_dataframe_measurements = str_path + "/" + str_datetime + " - Iteration 88 "   + "- TPT Measurements.csv"
                        df.to_csv(path_dataframe_measurements)
                else:
                    #Coreloss calculation here using integration
                    #Coreloss = calculate_coreloss(T1_ns,T2A_ns,T2B_ns,N_cycles,new_sample_time,data_voltage_primary,data_current_primary)
                    #print("Coreloss is", Coreloss, "J")

                    # Calculate acquisition chain (osc. + probes) limits to adjust the plots
                    tpt_settings.max_input_after_attenuation_A_V = 0.001 * max_channel_A_mV * voltage_probe_primary["attenuation"]
                    tpt_settings.max_input_after_attenuation_B_V = 0.001 * max_channel_B_mV * voltage_probe_secondary["attenuation"]
                    tpt_settings.max_input_after_attenuation_C_A = 0.001 * max_channel_C_mV * current_probe["attenuation"]

                    #Plot figures + draggable lines
                    fig1_handle, axs1 = tpt_calculations.plot_TPT_df_electrical(df, "Electrical variables vs. time - TPT acquisition", 1300, 975, scale_hw=True)
                    min_time = min(axs1[1].lines[0].get_xdata())
                    max_time = max(axs1[1].lines[0].get_xdata())
                    Vline1 = draggable_lines_class.draggable_lines(axs1[1], "h", I_0 + I_delta, [min_time, max_time])
                    Vline2 = draggable_lines_class.draggable_lines(axs1[1], "h", I_0 - I_delta, [min_time, max_time])
                    #from IPython import embed; embed()
                    plt.show()
                    #input("Now you can drag the lines in the current plot to check the stability.\nPress enter to continue.")
                    str_fig = str_path + "/" + str_datetime + " - Iteration " + str(n_iteration).zfill(2) + " - TPT.png"
                    fig1_handle.savefig(str_fig, dpi=tpt_settings.DPI)
                    print("Saving plot in path: ", str_fig)
                    path_parameters = str_path + "/" + str_datetime + " - Iteration " + str(n_iteration).zfill(2) + " - TPT Parameters.txt"
                    save_TPT_test_parameters(path_parameters, V_PSU_HIGH, V_PSU_LOW, V_pos, V_neg, T1_ns, T2A_ns,
                                             T2B_ns, T3_ns, N_cycles, actual_sample_time)
                    # Save dataframe to CSV file
                    path_dataframe_measurements = str_path + "/" + str_datetime + " - Iteration " + str(n_iteration).zfill(2) + " - TPT Measurements.csv"
                    df.to_csv(path_dataframe_measurements)
                    #from IPython import embed; embed()


            if(trigger_occurred is True):

                if (V_difference_error == 1):
                    continue_TPT_test = True
                else:
                    if(Group_or_Single_input):

                        Group_or_Single_count = Group_or_Single_count + 1
                        print("Group variable" , Group_or_Single_count)
                        continue_TPT_test = True
                        n_iteration = n_iteration + 1

                        # Print TPT data
                        print("Previous microcontroller + PSU parameters:")
                        micro_PSU_table = [['S (sign)', S, " "],
                                           ['T1', T1_ns, "ns"],
                                           ['T2A', T2A_ns, "ns"],
                                           ['T2B', T2B_ns, "ns"],
                                           ['T3', T3_ns, "ns"],
                                           ['N', N_cycles, " "],
                                           ['V_PSU_HIGH', V_PSU_HIGH, "V"],
                                           ['V_PSU_LOW', V_PSU_LOW, "V"]]
                        print(tabulate(micro_PSU_table, headers=['Variable', 'Value', 'Units'], tablefmt='psql'))
                        if (Group_or_Single_count < len(tpt_group_variables.Group_data)):
                            I_delta = tpt_group_variables.Group_data[Group_or_Single_count][0]
                            f_Hz = tpt_group_variables.Group_data[Group_or_Single_count][1] * 1000
                            I_0 = tpt_group_variables.Group_data[Group_or_Single_count][2]
                            V_PSU_HIGH, V_PSU_LOW, V_pos, V_neg, T1_ns, T2A_ns, T2B_ns, T3_ns = \
                                TPT_test_parameters_to_setup_parameters(L_uH, I_0, I_delta, D, f_Hz)
                            parameters_OK = TPT_check_parameters(L_uH, tpt_settings.saturation_current, I_0, I_delta, D,
                                                                 f_Hz, V_PSU_HIGH, V_PSU_LOW, T1_ns, T2A_ns, T2B_ns, T3_ns,
                                                                 N_cycles)
                            print("")
                            print("\nNew microcontroller + PSU parameters:")
                            micro_PSU_table = [['S (sign)', S, " "],
                                               ['T1', T1_ns, "ns"],
                                               ['T2A', T2A_ns, "ns"],
                                               ['T2B', T2B_ns, "ns"],
                                               ['T3', T3_ns, "ns"],
                                               ['N', N_cycles, " "],
                                               ['V_PSU_HIGH', V_PSU_HIGH, "V"],
                                               ['V_PSU_LOW', V_PSU_LOW, "V"]]
                            print(tabulate(micro_PSU_table, headers=['Variable', 'Value', 'Units'], tablefmt='psql'))
                            print("")
                        else:
                            print("All the Group variables has been successfully tested")
                            continue_TPT_test = False

                    else:

                        new_TPT_test = tpt_aux.prompt_question_yes_no("\nPerform a new TPT test with different parameters?", "N")

                        if(new_TPT_test):
                            continue_TPT_test = True
                            n_iteration = n_iteration + 1
                            # Print TPT data
                            print("Previous microcontroller + PSU parameters:")
                            micro_PSU_table = [['S (sign)', S, " "],
                                               ['T1', T1_ns, "ns"],
                                               ['T2A', T2A_ns, "ns"],
                                               ['T2B', T2B_ns, "ns"],
                                               ['T3', T3_ns, "ns"],
                                               ['N', N_cycles, " "],
                                               ['V_PSU_HIGH', V_PSU_HIGH, "V"],
                                               ['V_PSU_LOW', V_PSU_LOW, "V"]]
                            print(tabulate(micro_PSU_table, headers=['Variable', 'Value', 'Units'],  tablefmt='psql'))
                            V_PSU_HIGH = float(input("New V_PSU_HIGH [V]: "))
                            V_PSU_LOW = float(input("New V_PSU_LOW [V]: "))
                            T1_ns = int(input("New T1 [ns]: "))
                            if(S == 0):
                                V_pos = (2 * abs(I_delta)) * 1e-6 * L_uH / (T2A_ns * 1e-9)
                                V_neg = (2 * abs(I_delta)) * 1e-6 * L_uH / (T2B_ns * 1e-9)
                                T3_suggestion_ns = int(T1_ns * (V_pos / V_neg))
                            else:
                                V_pos = (2 * abs(I_delta)) * L_uH / (T2B_ns * 1e-9)
                                V_neg = (2 * abs(I_delta)) * L_uH / (T2A_ns * 1e-9)
                                T3_suggestion_ns = int(T1_ns * (V_neg / V_pos))
                            str_aux = "New T3 [ns] (suggestion: " + str(T3_suggestion_ns) + "): "
                            T3_ns = int(input(str_aux))
                            print("\nNew microcontroller + PSU parameters:")
                            micro_PSU_table = [['S (sign)', S, " "],
                                               ['T1', T1_ns, "ns"],
                                               ['T2A', T2A_ns, "ns"],
                                               ['T2B', T2B_ns, "ns"],
                                               ['T3', T3_ns, "ns"],
                                               ['N', N_cycles, " "],
                                               ['V_PSU_HIGH', V_PSU_HIGH, "V"],
                                               ['V_PSU_LOW', V_PSU_LOW, "V"]]
                            print(tabulate(micro_PSU_table, headers=['Variable', 'Value', 'Units'],  tablefmt='psql'))
                            print("")
                            parameters_OK = TPT_check_parameters(L_uH, tpt_settings.saturation_current, I_0, I_delta, D, f_Hz, V_PSU_HIGH, V_PSU_LOW, T1_ns, T2A_ns, T2B_ns, T3_ns, N_cycles)
                            if parameters_OK:
                                # Store parameters in global variables
                                tpt_settings.T1_ns = T1_ns
                                tpt_settings.T3_ns = T3_ns

                                # Definition of oscilloscope probes
                                max_voltage_primary = max(V_PSU_HIGH, V_PSU_LOW)
                                max_voltage_secondary = max_voltage_primary * turns_ratio
                                max_current_primary = abs(I_0) + abs(I_delta)

                                current_probe_ID = tpt_probes.get_probe_ID_enough_for("current", "max_input_current_peak", max_current_primary * 1.15)
                                voltage_probe_primary_ID = tpt_probes.get_probe_ID_enough_for("voltage", "max_input_positive", max_voltage_primary * 1.15)
                                voltage_probe_secondary_ID = tpt_probes.get_probe_ID_enough_for("voltage", "max_input_positive", max_voltage_secondary * 1.15)
                                print("--------------------------------------")
                                print("\nVerify that the following probes are connected to the oscilloscope:")
                                print("")
                                print("- CHANNEL 1 -")
                                print("Connect CH1 to the primary winding of the inductor")
                                voltage_probe_primary = tpt_probes.get_probe_from_ID(voltage_probe_primary_ID)
                                print(voltage_probe_primary["Description"])
                                print("\n- CHANNEL 2 -")
                                print("Connect CH2 to the secondary/auxiliary winding of the inductor")
                                voltage_probe_secondary = tpt_probes.get_probe_from_ID(voltage_probe_secondary_ID)
                                print(voltage_probe_secondary["Description"])
                                print("\n- CHANNEL 3 -")
                                current_probe = tpt_probes.get_probe_from_ID(current_probe_ID)
                                print(current_probe["Description"])
                                print("\n- CHANNEL 4 -")
                                print("Unused.")
                                print("")
                                print("--------------------------------------")
                                input("\nPress any key once the specified probes are in place.")
                                print("\n--------------------------------------")
                        else:
                            continue_TPT_test = False
                            # TPT calculations
                            # Save results to file
                            path_parameters = str_path + "/" + str_datetime + " - TPT Parameters.txt"
                            save_TPT_test_parameters(path_parameters, V_PSU_HIGH, V_PSU_LOW, V_pos, V_neg, T1_ns, T2A_ns, T2B_ns, T3_ns, N_cycles,actual_sample_time)
                            # Save dataframe to CSV file
                            path_dataframe_measurements = str_path + "/" + str_datetime + " - TPT Measurements.csv"
                            df.to_csv(path_dataframe_measurements)
            else:  #trigger did not occur
                print("The oscilloscope capture was not triggered.")
                repeat_TPT_test = tpt_aux.prompt_question_yes_no("Repeat the TPT test with the same parameters?", "N")
                if(repeat_TPT_test):
                    continue_TPT_test = True
                else:
                    # modify parameters
                    continue_TPT_test = False
                    print("We do not continue with the test")
                    # suggest modifying the parameters
    else: #parameter not ok
        print("Please specify the parameters within range.")
    if(tpt_settings.psusConnected):
        tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_HIGH, 0)
        tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_LOW, 0)
        tpt_PSU.disable_PSU(tpt_ser.ser_PSU_HIGH)
        tpt_PSU.disable_PSU(tpt_ser.ser_PSU_LOW)
    return test_results


def demagnetize_core():
    #给TL进行赋值
    tpt_micro.reset_microcontroller()
    print("Starting demagnetization process...")
    print("Warning (TODO) - check the signals with the ocilloscope (explanation in comment in for loop)")
    TL = math.floor(tpt_settings.saturation_time_ns)
    tpt_micro.modify_microcontroller_parameter("TL", TL)

    num_voltage_levels = 10
    demagnetization_voltages = np.linspace(0.8 * tpt_settings.saturation_average_voltage, 0.5, num=num_voltage_levels)
    # print("Debug 1: ", demagnetization_voltages)
    # from IPython import embed; embed()
                
    present_voltage = round(demagnetization_voltages[0], 3)
    #print("Debug 2: ", present_voltage)
    #from IPython import embed; embed()
    
    tpt_PSU.disable_PSU(tpt_ser.ser_PSU_HIGH)
    tpt_PSU.disable_PSU(tpt_ser.ser_PSU_LOW)
    tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_HIGH, present_voltage)
    tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_LOW, present_voltage)

    #print("Debug 3 - Check PSUs")
    #from IPython import embed; embed()
                
    tpt_PSU.enable_PSU(tpt_ser.ser_PSU_HIGH)
    tpt_PSU.enable_PSU(tpt_ser.ser_PSU_LOW)

    time.sleep(2)

    for i in range(0, demagnetization_voltages.size):
        present_voltage = round(demagnetization_voltages[i], 3)
        print("Applied voltage:", present_voltage, "V (level", i+1, "out of", num_voltage_levels, ")")            
        tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_HIGH, present_voltage)
        tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_LOW, present_voltage)
        tpt_micro.init_demagnetization_procedure_microcontroller()
        time.sleep(1)
        tpt_micro.stop_demagnetization_procedure_microcontroller()
        # TODO check with oscilloscope if these init/stop commands work well
        # otherwise we may need a reset between iterations...


    print("Disabling power supplies...")
    tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_HIGH, 0)
    tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_LOW, 0)
    tpt_PSU.disable_PSU(tpt_ser.ser_PSU_HIGH)
    tpt_PSU.disable_PSU(tpt_ser.ser_PSU_LOW)

    # only needed because of the bug in the firmware
    print("And resetting microcontroller...")
    tpt_micro.reset_microcontroller()    

'''

'''
def inductance_guided_test(chandle, pico_series):
#给出IGT页面，让L的值在5uH到3000uH之间，如果条件满足就把PSU的电压设到1V
    test_results = []
    clear_terminal()
    inductance_group = []
    print("+---------------------------------------------+")
    print("|            Guided inductance test           |")
    print("+---------------------------------------------+")
    print("")
    inductance_known = tpt_aux.prompt_question_yes_no("Is the small-signal inductance already known?", "Y")
    if(inductance_known):
        tpt_settings.inductance_apriori_uH = float(input("Inductance value [uH]: "))
        inductance_within_range = False
        if (tpt_settings.inductance_apriori_uH < 5):
            print("The inductance value is too low to be handled.")
            inductance_within_range = False
        elif (tpt_settings.inductance_apriori_uH > 3000):
            print("The inductance value is too high to be handled.")
            inductance_within_range = False
        elif ((tpt_settings.inductance_apriori_uH >= 5) and (tpt_settings.inductance_apriori_uH < 10)):
            PSU_voltage = 1
            inductance_within_range = True
            if(tpt_settings.psusConnected):
                print("Setting PSUs to 1 V...")
                tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_HIGH, 1)
                tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_LOW, 1)
            else:
                print("\nPlease set the Power Supply Units to 1 V")
                input("Press any key once the PSUs are set.")
        else:
            inductance_within_range = True
            PSU_voltage = round(0.5 * tpt_settings.inductance_apriori_uH * 1e-6 / 5e-6, 2)
            #这里是在算PSU的电压，但上面那个电压不是已经设定了吗，round（）是四舍五入function，这里这个else完全不知道什么时候能用到
            if(tpt_settings.psusConnected):
                print("Setting PSUs to", PSU_voltage, " V...")
                tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_HIGH, PSU_voltage)
                tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_LOW, PSU_voltage)
                tpt_PSU.enable_PSU(tpt_ser.ser_PSU_HIGH)
                tpt_PSU.enable_PSU(tpt_ser.ser_PSU_LOW)
            else:
                print("Please set the Power Supply Units to " + str(PSU_voltage) + " volts.")
                input("\nPress any key once the PSUs are set.")
        if inductance_within_range:
            # Folder and date strings 文件起名的
            str_datetime = tpt_aux.get_datetime_string()
            str_folder = str_datetime + " - Test Inductance"
            str_path = "Outputs/" + str_folder
            os.makedirs(str_path)
            
            # Definition of oscilloscope probes
            current_probe_ID = tpt_probes.get_probe_ID_largest_parameter("current", "max_input_current_peak")
            voltage_probe_ID = tpt_probes.get_probe_ID_enough_for("voltage", "max_input_positive", PSU_voltage * 1.15)
            voltage_probe_ID = "V6"
            current_probe_ID = "C2"
            #上面这两行代码其实是可以调整的，他是根据需求选probe，下面代码是推荐你用什么probe，不是识别probe
            print("--------------------------------------")
            print("\nVerify that the following probes are connected to the oscilloscope:")
            print("")
            print("- CHANNEL 1 -")
            voltage_probe = tpt_probes.get_probe_from_ID(voltage_probe_ID)
            print(voltage_probe["Description"])
            print("")
            print("- CHANNEL 2 -")
            print("Do not connect CH2 to the secondary/auxiliary winding of the inductor")
            print("")
            print("- CHANNEL 3 -")
            current_probe = tpt_probes.get_probe_from_ID(current_probe_ID)
            print(current_probe["Description"])
            print("")
            print("- CHANNEL 4 -")
            print("Unused.")
            print("")
            print("--------------------------------------")
            input("\nPress any key once the specified probes are in place.")
            print("\n--------------------------------------")

            # Frequency range and TL values

            min_frequency = 300     # Hz - Theoretical limit due to dead-time resolution: 286 Hz (temporary, pending fix)
            max_frequency = 100000  # Hz
            frequencies_per_decade = 12
            r = 10 ** (1/frequencies_per_decade)    # frequency ratio
            # Create list of frequencies in the sweep
            # 从高往低扫频率，每次将最高频率除以r值，一直除到最小值，默认赋值中r大概是1.2，我也不知道为什么要这么测
            sweep_frequencies = []
            current_frequency = max_frequency
            while(current_frequency > min_frequency):
                sweep_frequencies.append(current_frequency)
                current_frequency = round(current_frequency / r, 5)
            sweep_frequencies.append(min_frequency)
            # Create list of TL values in the sweep (in nanoseconds)
            sweep_TL = []
            for i in range(0, len(sweep_frequencies)):
                TL = int(round(1000000000 / (sweep_frequencies[i] * 2), 0))
                sweep_TL.append(TL)

            #from IPython import embed; embed()

            # Oscilloscope variables
            status = {}
            enabled = 1
            disabled = 0
            analogue_offset = 0.0

            # Iterate over sweep_TL performing the acquisition and calculations
            core_has_saturated = False
            continue_inductance_test = True
            n = 0

            while (not(core_has_saturated) and continue_inductance_test):

                print("\n - Iteration ", n, " -")
                TL = sweep_TL[n]

                # Store parameter in microcontroller memory
                #time_pre = time.time_ns()
                tpt_micro.modify_microcontroller_parameter("TL", TL)
                #time_post = time.time_ns()
                #print("The microcontroller message took: ", time_post - time_pre, " ns")
                time.sleep(0.5)

                # Set up oscilloscope channels
                # Set up channel A (VOLTAGE)
                max_input_voltage_A = 1.15 * PSU_voltage / voltage_probe["attenuation"]
                channel_range_A, _ = tpt_osc.get_oscilloscope_input_range(max_input_voltage_A, pico_series)
                tpt_osc.print_channel_range("A", channel_range_A, pico_series)
                channel_A_code = tpt_osc.oscilloscope_range_str2code("A", pico_series)
                tpt_osc.set_oscilloscope_channel_range(chandle, 
                                                       channel_A_code, 
                                                       channel_range_A,
                                                       analogue_offset,
                                                       pico_series)
                #不太确定chandle是啥

                # Set up channel B (unused)
                channel_B_code = tpt_osc.oscilloscope_range_str2code("B", pico_series)
                tpt_osc.disable_oscilloscope_channel(chandle,
                                                     channel_B_code,
                                                     pico_series)

                # Set up channel C (CURRENT)
                # We know the maximum current that will be reached as long as the core does not
                # saturate. But since we are actually trying to saturate it, we need to leave 
                # some room for the sudden current increase. I suggest a 50% over the expected max current
                expected_current = TL * 1e-9 * PSU_voltage / (tpt_settings.inductance_apriori_uH * 1e-6)
                #i = V*dt/L
                max_input_voltage_C = 1.4 * expected_current / current_probe["attenuation"]  # conversion V-A 留出空余
                channel_range_C, _ = tpt_osc.get_oscilloscope_input_range(max_input_voltage_C, pico_series)
                #channel_range_C = ps2.PS2000A_RANGE['PS2000A_1V']  #debug to acquire Teensy pulses, remove line
                tpt_osc.print_channel_range("C", channel_range_C, pico_series)
                channel_C_code = tpt_osc.oscilloscope_range_str2code("C", pico_series)
                tpt_osc.set_oscilloscope_channel_range(chandle, 
                                                       channel_C_code, 
                                                       channel_range_C,
                                                       analogue_offset,
                                                       pico_series)

                # Set up channel D (unused)
                channel_D_code = tpt_osc.oscilloscope_range_str2code("D", pico_series)
                tpt_osc.disable_oscilloscope_channel(chandle,
                                                     channel_D_code,
                                                     pico_series)

                # Trigger configuration

                # Finds the max ADC count, and max level per channel
                # handle = chandle
                # Value = ctype.byref(maxADC)
                maxADC = ctypes.c_int16()
                tpt_osc.check_oscilloscope_maximum_value(chandle, maxADC, pico_series)
                # bufferAMax = (ctypes.c_int16 * total_samples)()
                #from IPython import embed; embed()
                max_channel_A_mV = tpt_osc.adc2mV_single_value(maxADC.value, channel_range_A, maxADC, pico_series)
                max_channel_C_mV = tpt_osc.adc2mV_single_value(maxADC.value, channel_range_C, maxADC, pico_series)
                # threshold level: roughly one-fifth of PSU voltage - specified for the oscilloscope in ADC counts
                trigger_level = max_input_voltage_A / 5  # in volts
                trigger_threshold_counts = mV2adc(1000 * trigger_level, channel_range_A, maxADC)
                print("Trigger level: ", mV2adc(1000 * trigger_level, channel_range_A, maxADC), " mV (", "%s%%"%int(round(100 * trigger_threshold_counts / maxADC.value, 0)), " over the full input range)")
                trigger_occurred = False
                #print("Debug - Trigger level: ", trigger_threshold_counts, "counts out of a maximum of ", maxADC.value)
                # channel_range_A               
                
                # Sets up single trigger
                tpt_osc.set_oscilloscope_trigger(chandle, channel_A_code, trigger_threshold_counts, "RISING", pico_series)

                desired_sample_time = math.floor(TL / 1000)  # ns
                desired_pre_trigger_samples = 100
                desired_post_trigger_samples = 2500  # 2.5 times TL
                active_channels = 2  # obtain by code?

                timebase_OK, n_timebase, actual_sample_time, actual_pre_trigger_samples, actual_post_trigger_samples \
                    = tpt_osc.calculate_oscilloscope_time_config(chandle, desired_pre_trigger_samples, desired_post_trigger_samples, desired_sample_time, active_channels, pico_series)

                total_samples = actual_pre_trigger_samples + actual_post_trigger_samples

                print("TL value (half period of pulse): ", TL, " ns")
                print("Attempted sample time: ", desired_sample_time, " ns")
                print("Actual sample time: ", actual_sample_time, " ns (timebase(n): ", n_timebase, ")")
                # print("Timebase (n): ", n_timebase)
                print("Number of samples per half period of pulse: ", math.floor(TL / actual_sample_time))
                print("Total samples: ", total_samples)
                
                if(timebase_OK):
                    tpt_osc.run_oscilloscope_block_acquisition(chandle, actual_pre_trigger_samples, actual_post_trigger_samples, n_timebase, pico_series)

                    # Launch inductance test (Teensy)
                    print("Sending command to the microcontroller...")
                    tpt_micro.execute_Inductance_Test_microcontroller()

                    # Check for data collection to finish
                    tpt_osc.wait_oscilloscope_acquisition_ready(chandle, pico_series)

                    print("Retrieving data from the oscilloscope...")
                    # Create buffers ready for assigning pointers for data collection
                    bufferAMax = (ctypes.c_int16 * total_samples)()
                    bufferCMax = (ctypes.c_int16 * total_samples)()

                    # Set data buffer location for data collection from channels A and C
                    tpt_osc.set_data_buffers(chandle, channel_A_code, bufferAMax, total_samples, pico_series)
                    tpt_osc.set_data_buffers(chandle, channel_C_code, bufferCMax, total_samples, pico_series)
                    
                    # Create overflow location
                    overflow = ctypes.c_int16()
                    # create converted type totalSamples
                    c_total_samples = ctypes.c_int32(total_samples)
                    
                    # Assign retrieved data from scope to buffers assigned above
                    tpt_osc.get_oscilloscope_values(chandle, c_total_samples, overflow, pico_series)

                    # convert ADC counts data to mV, and then to numpy array
                    adc2mVChAMax = adc2mV(bufferAMax, channel_range_A, maxADC)
                    adc2mVChCMax = adc2mV(bufferCMax, channel_range_C, maxADC)
                    A_array = np.array(adc2mVChAMax)  # Value in mV
                    C_array = np.array(adc2mVChCMax)  # Value in mV

                    # convert to volt and reverse attenuation
                    A_array = 0.001 * A_array * voltage_probe["attenuation"]
                    C_array = 0.001 * C_array * current_probe["attenuation"]

                    # Create time data
                    #time_array_ns = np.linspace(0, ((c_total_samples.value)-1) * timeIntervalns.value, c_total_samples.value)
                    time_array_ns = np.linspace(0, ((c_total_samples.value)-1) * actual_sample_time, c_total_samples.value)
                    time_array_plot = time_array_ns
                    str_time = "Time (ns)"
                    max_time_ns = max(time_array_ns)
                    if(max_time_ns > 25000 and max_time_ns <= 25000000):
                        time_array_plot = time_array_plot / 1000
                        str_time = "Time (us)"
                    elif(max_time_ns > 25000000 and max_time_ns < 25000000000):
                        time_array_plot = time_array_plot / 1000000
                        str_time = "Time (ms)"
                    
                    # plot data from channel A and C
                    plt.subplot(2, 1, 1)  # row 1, col 2 index 1
                    plt.plot(time_array_plot, A_array)  # volts
                    plt.ylim([-0.001 * max_channel_A_mV * voltage_probe["attenuation"], 0.001 * max_channel_A_mV * voltage_probe["attenuation"]])
                    plt.xlabel(str_time)
                    plt.tight_layout()
                    plt.ylabel('Voltage (V)')

                    plt.subplot(2, 1, 2)  # row 1, col 2 index 1
                    plt.plot(time_array_plot, C_array)  # amps
                    plt.ylim([-0.001 * max_channel_C_mV * current_probe["attenuation"], 0.001 * max_channel_C_mV * current_probe["attenuation"]])
                    plt.xlabel(str_time)
                    plt.tight_layout()
                    plt.ylabel('Current (A)')
                    print("Plotting graphs.")
                    figure = plt.gcf() # get current figure
                    pixels_h = 1300
                    pixels_v = 975
                    figure.set_size_inches(pixels_h / tpt_settings.DPI, pixels_v / tpt_settings.DPI)
                    figure.set_size_inches(8, 6)
                    plt.ion()
                    plt.show(block=False)
                    #plt.pause(3)
                    plt.pause(0.1)
                    
                    str_fig = str_path + "/" + str_datetime + " - Iteration " + str(n).zfill(2) + " - TL=" + str(TL) + "ns.png"
                    #str_fig = "Figures/Figure" + str(n) + " - TL=" + str(TL) + "ns.png"
                    plt.savefig(str_fig, dpi=tpt_settings.DPI)
                    print(str_fig)
                    #from IPython import embed; embed()
                    trigger_occurred = tpt_osc.determine_if_trigger_occurred(A_array, trigger_level)

                    #time.sleep(2) # debug 
                    #plt.close(1) # debug
                    # # Show graph
                    # # based on the average level / measurements, decide if a trigger event has occured or not
                    # # the autoTrigger is set to 5000 ms, enough time to launch the pulse.
                    # # Ask the user to continue or not

                if(trigger_occurred is True):
                    saturation_reached, saturation_parameters = determine_if_inductor_saturated(C_array, A_array, actual_pre_trigger_samples, actual_sample_time, TL)
                    #print("Number of samples per half period of pulse: ", math.floor(TL / actual_sample_time))
                    if(saturation_reached is True):
                        tpt_settings.saturation_calculated = True
                        tpt_settings.saturation_current = saturation_parameters[0]
                        tpt_settings.saturation_average_voltage = saturation_parameters[1]
                        tpt_settings.saturation_time_ns = saturation_parameters[2]
                        print("The saturation current of the inductor is ", round(tpt_settings.saturation_current, 3), "Amps")
                        continue_inductance_test = False
                        # Calculate inductance
                        tpt_settings.inductance_large_signal_uH = 1e6 * calculate_large_signal_inductance(A_array, C_array, actual_pre_trigger_samples, actual_sample_time, TL)
                        print("The large-signal inductance of the inductor is ", tpt_settings.inductance_large_signal_uH, "uH")
                        # Save results to text file
                        path_results = str_path + "/" + str_datetime + " - Inductance Results.txt"
                        save_inductance_test_results(path_results, tpt_settings.inductance_large_signal_uH, tpt_settings.saturation_current, tpt_settings.saturation_average_voltage)
                    else:
                        n = n + 1
                        if(n < len(sweep_TL)): 
                            time.sleep(2)
                            plt.close(1)  # debug
                            continue_inductance_test = True
                            #continue_inductance_test = prompt_question_yes_no("Continue the inductance test with the next period?", "Y")
                        else:
                            print("\nTest finished across all the predefined periods.")
                            continue_inductance_test = False
                else:  #trigger did not occur
                    print("The oscilloscope capture was not triggered.")
                    repeat_inductance_test = tpt_aux.prompt_question_yes_no("Repeat the inductance test with the same period?", "Y")
                    if(repeat_inductance_test):
                        continue_inductance_test = True
                    else:
                        skip_untriggered_period = tpt_aux.prompt_question_yes_no("Continue the inductance test with the next period?", "Y")
                        #from IPython import embed; embed()
                        if (skip_untriggered_period == True):
                            n = n + 1
                            if(n < len(sweep_TL)):
                                continue_inductance_test = True
                            else:
                                continue_inductance_test = False
                        else:
                            continue_inductance_test = False
                            print("\nPlease enter to leave the inductance test.")
    else:
        print("This feature is not implemented yet")
    if(tpt_settings.psusConnected):
        tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_HIGH, 0)
        tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_LOW, 0)
        tpt_PSU.disable_PSU(tpt_ser.ser_PSU_HIGH)
        tpt_PSU.disable_PSU(tpt_ser.ser_PSU_LOW)    
    input("\nPress any key to continue.")
    plt.close('all')

def determine_if_inductor_saturated(current_array_raw, voltage_array_raw, actual_pre_trigger_samples, actual_sample_time, TL):
    samples_per_half_period = math.floor(TL / actual_sample_time)
    i_start = actual_pre_trigger_samples + 1
    i_stop = actual_pre_trigger_samples + 1 + samples_per_half_period
    #eliminate first glitches (10% of waveform)
    i_start = i_start + math.floor((i_stop - i_start) * 0.1)

    current_array = current_array_raw[i_start:i_stop]
    voltage_array = voltage_array_raw[i_start:i_stop]
    x_array = np.linspace(0, current_array.size-1, num=current_array.size)
    _, _, r_value, _, _ = stats.linregress(x_array, current_array)
    print("R-squared value: ", r_value)
    saturation_parameters = [0, 0, 0] # [saturation current, average voltage to saturation, time to saturation (ns)]
    if(r_value > 0.96):
        saturation_reached = False
    else:
        saturation_reached = True
        # divide arrays in two: first half -> extract slope and intercept
        # substract trendline from actual current
        # saturation current: when error > threshold [%]
        n_half = math.floor(x_array.size / 2)
        x_array_half = x_array[0:n_half]
        current_array_half = current_array[0:n_half]
        slope, intercept, _, _, _ = stats.linregress(x_array_half, current_array_half)
        ideal_current_array = np.zeros(x_array.size)
        rel_error_array = np.zeros(x_array.size)
        for i in range(0, x_array.size):
            ideal_current_array[i] = slope * i + intercept
            rel_error_array[i] = abs((ideal_current_array[i] - current_array[i]) / ideal_current_array[i])
        # find first occurrence where relative error is larger than 0.4
        saturation_current = current_array[np.argmax(rel_error_array > 0.4)]
        #from IPython import embed; embed()
        # find mean applied voltage
        voltage_array_half = voltage_array[0:n_half]
        saturation_average_voltage = np.mean(voltage_array_half)
        # find time to saturation
        saturation_time_ns = actual_sample_time * np.argmax(rel_error_array > 0.4)
        saturation_parameters = [saturation_current, saturation_average_voltage, saturation_time_ns]  
    return saturation_reached, saturation_parameters


def calculate_large_signal_inductance(voltage_array_raw, current_array_raw, actual_pre_trigger_samples, actual_sample_time, TL):
    # Calculates the large signal inductance taking the first half of the inductor current (applied voltage / slope)
    samples_per_half_period = math.floor(TL / actual_sample_time)
    i_start = actual_pre_trigger_samples + 1
    i_stop = actual_pre_trigger_samples + 1 + samples_per_half_period
    # eliminate first glitches (10% of waveform)
    i_start = i_start + math.floor((i_stop - i_start) * 0.1)
    # eliminate second half of half-period (so we'll be roughly taking 45 % of the positive pulse): 
    # 10% (glithes) + 45 % (calculation) + 45% (discarded because there may be saturation)
    i_stop = i_start + math.floor((i_stop - i_start)/2)
    current_array = current_array_raw[i_start:i_stop]
    voltage_array = voltage_array_raw[i_start:i_stop]
    x_array = np.linspace(0, current_array.size-1, num=current_array.size) * actual_sample_time * 1e-9

    # Calculate waveform parameters
    slope, _, _, _, _ = stats.linregress(x_array, current_array)
    average_voltage = np.mean(voltage_array)
    inductance_large_signal = (average_voltage) / slope
    print("Average voltage",average_voltage,"Slope",slope, x_array[-1])
    #from IPython import embed; embed()
    return inductance_large_signal


def TPT_test_parameters_to_setup_parameters(L_uH, I_0, I_delta, D, f_Hz):
    # L_uH [uH] 
    # I_0 [A]: DC current level
    # I_delta [A]: I_max - I_0 (i.e., the total current swing is 2 * I_delta)
    # D: duty cycle (0...1)
    # f_Hz [Hz]: frequency
    L_H = L_uH * 1e-6
    T_s = 1 / f_Hz
    T2A_ns = 1e9 * D * T_s
    T2B_ns = 1e9 * (1 - D) * T_s

    if(I_0 >= 0):
        V_pos = (2 * abs(I_delta)) * L_H / (T2A_ns * 1e-9)
        V_neg = (2 * abs(I_delta)) * L_H / (T2B_ns * 1e-9)

        T1_ns = 1e9 * L_H * (abs(I_0) + abs(I_delta)) / V_pos
        # Over the whole test: integral of V over t must be zero
        # T1 * V_pos - T2B * V_neg  - T3 * V_neg = 0
        # T3_ns = T1_ns * (V_pos / V_neg) - T2B_ns (with old chronogram 12/08/22)
        T3_ns = T1_ns * (V_pos / V_neg)  # with new chronogram for stage IV (12/08/22)

        R_switch = 0.075  # TODO verify that this approach is correct / missing diode drop
        R_switch = 0
        V_diode = 0  # TODO correct diode drop
        V_PSU_HIGH = round(V_pos + I_0 * R_switch + V_diode, 3)
        V_PSU_LOW = round(V_neg + I_0 * R_switch + V_diode, 3)
    else:
        V_pos = (2 * abs(I_delta)) * L_H / (T2B_ns * 1e-9)
        V_neg = (2 * abs(I_delta)) * L_H / (T2A_ns * 1e-9)
        T1_ns = 1e9 * L_H * (abs(I_0) + abs(I_delta)) / V_neg
        # T3_ns = T1_ns * (V_neg / V_pos) - T2B_ns  (with old chronogram 12/08/22)
        T3_ns = T1_ns * (V_neg / V_pos)  # with new chronogram for stage IV (12/08/22)

        R_switch = 0.075  # TODO verify that this approach is correct / missing diode drop
        R_switch = 0
        V_diode = 0  # TODO correct diode drop
        V_PSU_HIGH = round(V_pos + I_0 * R_switch + V_diode, 3)
        V_PSU_LOW = round(V_neg + I_0 * R_switch + V_diode, 3)
    return V_PSU_HIGH, V_PSU_LOW, V_pos, V_neg, T1_ns, T2A_ns, T2B_ns, T3_ns


def TPT_check_parameters(L_uH, I_sat, I_0, I_delta, D, f_Hz, V_PSU_HIGH, V_PSU_LOW, T1_ns, T2A_ns, T2B_ns, T3_ns, N):
    I_max = abs(I_0) + abs(I_delta)
    if(I_max <= tpt_settings.HALF_BRIDGE_max_current):
        I_max_HALF_BRIDGE_OK = True
    else:
        I_max_HALF_BRIDGE_OK = False

    if(I_max <= I_sat):
        I_max_sat_OK = True
    else:
        I_max_sat_OK = False

    if(D >= tpt_settings.MICROCONTROLLER_min_duty_cycle):
        D_min_OK = True
    else:
        D_min_OK = False

    if(D <= tpt_settings.MICROCONTROLLER_max_duty_cycle):
        D_max_OK = True
    else:
        D_max_OK = False

    if(f_Hz >= tpt_settings.MICROCONTROLLER_min_frequency):
        frequency_min_OK = True
    else:
        frequency_min_OK = False

    if(f_Hz <= tpt_settings.MICROCONTROLLER_max_frequency):
        frequency_max_OK = True
    else:
        frequency_max_OK = False

    if(N >= tpt_settings.MICROCONTROLLER_min_N):
        N_min_OK = True
    else:
        N_min_OK = False

    if(N <= tpt_settings.MICROCONTROLLER_max_N):
        N_max_OK = True
    else:
        N_max_OK = False

    if(abs(V_PSU_HIGH) <= tpt_settings.PSU_HIGH_max_voltage):
        V_PSU_HIGH_OK = True
    else:
        V_PSU_HIGH_OK = False

    if(abs(V_PSU_LOW) <= tpt_settings.PSU_LOW_max_voltage):
        V_PSU_LOW_OK = True
    else:
        V_PSU_LOW_OK = False

    # Print results
    table = [['I max (vs. Half-Bridge) [A]', I_max, tpt_settings.HALF_BRIDGE_max_current, I_max_HALF_BRIDGE_OK],
             ['I max (vs. Saturation) [A]', I_max, I_sat, I_max_sat_OK],
             ['Duty cycle min', D, tpt_settings.MICROCONTROLLER_min_duty_cycle, D_min_OK],
             ['Duty cycle max', D, tpt_settings.MICROCONTROLLER_max_duty_cycle, D_max_OK],
             ['Frequency min [Hz]', f_Hz, tpt_settings.MICROCONTROLLER_min_frequency, frequency_min_OK],
             ['Frequency max [Hz]', f_Hz, tpt_settings.MICROCONTROLLER_max_frequency, frequency_max_OK],
             ['N min', N, tpt_settings.MICROCONTROLLER_min_N, N_min_OK],
             ['N max', N, tpt_settings.MICROCONTROLLER_max_N, N_max_OK],
             ['PSU HIGH [V]', V_PSU_HIGH, tpt_settings.PSU_HIGH_max_voltage, V_PSU_HIGH_OK],
             ['PSU LOW [V]', V_PSU_LOW, tpt_settings.PSU_LOW_max_voltage, V_PSU_LOW_OK]]

    print(tabulate(table, headers=['Variable', 'Value', 'Compare value', 'Within limits?'],  tablefmt='psql'))

    parameters_OK = I_max_HALF_BRIDGE_OK and I_max_sat_OK and D_min_OK and D_max_OK and frequency_min_OK \
        and frequency_max_OK and N_min_OK and N_max_OK and V_PSU_HIGH_OK and V_PSU_LOW_OK
    
    if(parameters_OK):
        print('TPT parameters are OK.')
    else:
        print('One or more TPT parameters are outside the limits.')
    return parameters_OK


def save_inductance_test_results(path_results, inductance_large_signal_uH, saturation_current, saturation_average_voltage):
    table = [[inductance_large_signal_uH, saturation_current, saturation_average_voltage]]
    if not os.path.exists("Outputs/"):
        os.mkdir("Outputs/")
    with open(path_results, 'w') as outputfile:
        outputfile.write(tabulate(table, headers=['Large-signal inductance [uH]', 'Saturation current [A]', 'Average test voltage [V]'],  tablefmt='psql'))


def save_TPT_test_parameters(path_parameters, V_PSU_HIGH, V_PSU_LOW, V_pos, V_neg, T1_ns, T2A_ns, T2B_ns, T3_ns, N_cycles, actual_sampling_time):
    # saves a readable file with the test parameters
    # as well as a df with the variables in csv format
    table = [["Large-signal inductance [uH]", round(tpt_settings.inductance_large_signal_uH, 3)],
             ["Saturation current [A]", round(tpt_settings.saturation_current, 3)],
             ["Actual sample time [ns]", round(tpt_settings.Actual_sample_time,3)],
             ["V_PSU_HIGH [V]", round(V_PSU_HIGH, 3)],
             ["V_PSU_LOW [V]", round(V_PSU_LOW, 3)],
             ["V_pos (calculated) [V]", round(V_pos, 3)],
             ["V_pos (measured) [V]", "TBD"],
             ["V_neg (calculated) [V]", round(V_neg, 3)],
             ["V_neg (measured) [V]", "TBD"],
             ["T1 [ns]", round(tpt_settings.T1_ns)],
             ["T2A [ns]", round(tpt_settings.T2A_ns)],
             ["T2B [ns]", round(tpt_settings.T2B_ns)],
             ["T3 [ns]", round(tpt_settings.T3_ns)],
             ["N", round(tpt_settings.N_cycles)],
             ["N1", tpt_settings.N1],
             ["N2", tpt_settings.N2],
             ["Ae [m2]", round(tpt_settings.Ae, 4)],
             ["le [m]", round(tpt_settings.le, 6)],
             ["Ve [m3]", round(tpt_settings.Ve, 8)],
             ["More stuff", "TBD"]]

    if not os.path.exists("Outputs/"):
        os.mkdir("Outputs/")

    with open(path_parameters, 'w') as outputfile:
        outputfile.write(tabulate(table, headers=['Parameter', 'Value'],  tablefmt='psql'))

    df_params = pd.DataFrame()
    df_params["inductance_large_signal_uH"] = [tpt_settings.inductance_large_signal_uH]
    df_params["saturation_current"] = [tpt_settings.saturation_current]
    df_params["actual_sample_time"] = [tpt_settings.Actual_sample_time]
    df_params["V_PSU_HIGH"] = [V_PSU_HIGH]
    df_params["V_PSU_LOW"] = [V_PSU_LOW]
    df_params["V_pos_calculated"] = [V_pos]
    df_params["V_neg_calculated"] = [V_neg]
    df_params["T1_ns"] = [T1_ns]
    df_params["T2A_ns"] = [T2A_ns]
    df_params["T2B_ns"] = [T2B_ns]
    df_params["T3_ns"] = [T3_ns]
    df_params["N_cycles"] = [N_cycles]
    df_params["Ae"] = [tpt_settings.Ae]
    df_params["le"] = [tpt_settings.le]
    df_params["Ve"] = [tpt_settings.Ve]
    df_params["N1"] = [tpt_settings.N1]
    df_params["N2"] = [tpt_settings.N2]
    df_params["T1_ns"] = [tpt_settings.T1_ns]
    df_params["T2A_ns"] = [tpt_settings.T2A_ns]
    df_params["T2B_ns"] = [tpt_settings.T2B_ns]
    df_params["T3_ns"] = [tpt_settings.T3_ns]
    df_params["N_cycles"] = [tpt_settings.N_cycles]
    df_params["S"] = [tpt_settings.S]
    df_params.to_csv(path_parameters[0:-3] + "csv")


def load_TPT_test_parameters(df_params):
    tpt_settings.inductance_large_signal_uH = df_params["inductance_large_signal_uH"][0]
    tpt_settings.saturation_current = df_params["saturation_current"][0]
    tpt_settings.Actual_sample_time = df_params["actual_sample_time"]
    tpt_settings.Ae = df_params["Ae"][0]
    tpt_settings.le = df_params["le"][0]
    tpt_settings.Ve = df_params["Ve"][0]
    tpt_settings.N1 = df_params["N1"][0]
    tpt_settings.N2 = df_params["N2"][0]
    tpt_settings.T1_ns = df_params["T1_ns"][0]
    tpt_settings.T2A_ns = df_params["T2A_ns"][0]
    tpt_settings.T2B_ns = df_params["T2B_ns"][0]
    tpt_settings.T3_ns = df_params["T3_ns"][0]
    tpt_settings.N_cycles = df_params["N_cycles"][0]
    tpt_settings.S = df_params["S"][0]

def clear_terminal():
    os.system('cls' if os.name == 'nt' else 'clear')

###########################################################################################################################################
##Test area
def calculate_coreloss(T1,T2A,T2B,N_cycle,new_sample_time,voltage_array,current_array):

    V_start = math.floor((T1+(N_cycle-2) * (T2A+T2B)+0.5 * T2A) * (1/new_sample_time))
    V_end = math.floor((T1+(N_cycle-2) * (T2A+T2B)+0.5 * T2B+T2A) * (1/new_sample_time))
    Peak_finding_array = voltage_array[V_start:V_end]
    max_value = np.max(Peak_finding_array)  # 找到最大值
    max_index = np.argmax(Peak_finding_array)  # 找到最大值的位置
    V_actual_start = 1+max_index+V_start
    V_actual_end = V_actual_start+math.floor((T2A+T2B)*(1/new_sample_time))
    current_array = current_array[V_actual_start:V_actual_end]
    voltage_array = voltage_array[V_actual_start:V_actual_end]
    i = 0
    Q_loss = [0] * 1000000000
    for i in range (0, len(voltage_array)):
        Q_loss[i] = voltage_array[i] * current_array[i]
        i = i+1
    Q_sum = np.sum(Q_loss)
    P = Q_sum * new_sample_time * 1e-09

    return P

def compare_voltage_level(T1,T2A,T2B,N_cycle,new_sample_time,voltage_array):

    V_start_low = math.floor((T1 + (N_cycle - 2) * (T2A + T2B) + 0.1 * T2A) * (1 / new_sample_time))
    V_end_low = math.floor((T1 + (N_cycle - 2) * (T2A + T2B) + 0.9 * T2A) * (1 / new_sample_time))
    Mean_finding_array_low = voltage_array[V_start_low:V_end_low]
    mean_low = np.mean(Mean_finding_array_low)
    V_start_high = math.floor((T1 + (N_cycle - 2) * (T2A + T2B) + T2A + 0.1 * T2B) * (1 / new_sample_time))
    V_end_high = math.floor((T1 + (N_cycle - 2) * (T2A + T2B) + T2A + 0.9 * T2B) * (1 / new_sample_time))
    Mean_finding_array_high = voltage_array[V_start_high:V_end_high]
    mean_high = np.mean(Mean_finding_array_high)
    mean_difference = mean_high + mean_low

    return mean_difference

def TPT_group_variables_setup():

    L_uH = 264
    D = 0.5
    tpt_settings.saturation_current = 3
    N_cycles = 4

    tpt_group_variables.Group_frequency = [20, 40]
    tpt_group_variables.Group_DC_bias = [0.5, 1]
    tpt_group_variables.Group_I_delta = [0.5, 1]
    tpt_group_variables.Group_data = []

    for I_delta in tpt_group_variables.Group_I_delta:
        for frequency in tpt_group_variables.Group_frequency:
            for dc_bias in tpt_group_variables.Group_DC_bias:
                combination = [I_delta, frequency, dc_bias]
                tpt_group_variables.Group_data.append(combination)
    print("+---------------------------------------------+")
    print("| Please confirm the test conditions you would like to test |")
    print(" The value will be listed in the order of \nI_delta [A], Frequency [kHz], DC-bias condition [A]")
    print("+---------------------------------------------+")
    for i in range(len(tpt_group_variables.Group_data)):
        print("\nNo.", i, "--------",)
        for j in range(len(tpt_group_variables.Group_data[i])):
            print(tpt_group_variables.Group_data[i][j], end=' ')
    input("\nPress any key to proceed with verifying the parameters.")
    for i in range(len(tpt_group_variables.Group_data)):
        temp_I_delta = tpt_group_variables.Group_data[i][0]
        temp_frequency = tpt_group_variables.Group_data[i][1] * 1000
        temp_dc_bias = tpt_group_variables.Group_data[i][2]
        V_PSU_HIGH, V_PSU_LOW, V_pos, V_neg, T1_ns, T2A_ns, T2B_ns, T3_ns = \
            TPT_test_parameters_to_setup_parameters(L_uH, temp_dc_bias, temp_I_delta, D, temp_frequency)
        parameters_OK = TPT_check_parameters(L_uH, tpt_settings.saturation_current, temp_dc_bias, temp_I_delta, D, temp_frequency, V_PSU_HIGH,
                                             V_PSU_LOW, T1_ns, T2A_ns, T2B_ns, T3_ns, N_cycles)
        print("")
        if not (parameters_OK):
            print("I_delta", temp_I_delta, "Frequency", temp_frequency, "DC-bias condition", temp_dc_bias,"--", "This set of variables is out of limit")
            print("Please alter the value in tpt_automation.py file before proceed to further testing")
            break








