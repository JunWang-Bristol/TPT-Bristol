import glob
import serial  # pySerial, not Serial (important)
import time
import os

import IPython
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.widgets import SpanSelector

import win32com.client  # pip install pypiwin32

import ctypes
import numpy as np
from picosdk.ps2000a import ps2000a as ps2
from picosdk.ps3000a import ps3000a as ps3

from picosdk.errors import PicoSDKCtypesError
from picosdk.functions import adc2mV, mV2adc, assert_pico_ok

import tpt_PSU
import tpt_micro
import tpt_settings
import tpt_ser
import tpt_aux
import tpt_automation
import tpt_calculations

def Init():
    # Calculate 
    # unsigned int sign;          //0: positive / 1: negative
    # unsigned int T_boost_off;      //Used for positive B_offset
    # unsigned int T_boost_on;      //Used for positive B_offset
    # unsigned int T1;           // time constants in microseconds
    # unsigned int T2_on; 
    # unsigned int T2_off;
    # unsigned int T3;           //T1-T2

    clear_terminal()
    tpt_settings.init() 
    tpt_settings.init()

    inProgram = True
    
    ###################################################################
    #                    HARDWARE INITIALIZATION                      #
    ###################################################################

    #                         = TEENSY =
    # Configure Serial port
    # Serial port initialization
    wmi = win32com.client.GetObject("winmgmts:")
    ports = tpt_micro.serial_ports()
    # future improvement
    # if size > 1 -> prompt user to select one
    # otherwise -> portIndex = 0
    if(len(ports) == 0):
        print("No available serial ports were found.")
        print("Please make sure to connect the required devices.")
        # input("\nPress any key to continue.")
        # inProgram = False
    else:
        print("List of available serial ports:")
        #print(ports)
        # Identify USB devices with their VID / PID codes
        for serialPorts in wmi.InstancesOf("Win32_SerialPort"):
            print("-----------------------------------------------")
            print(serialPorts.PNPDeviceID)
            str_PNPDeviceID = serialPorts.PNPDeviceID
            i_str_VID = str_PNPDeviceID.find("VID")
            str_VID = str_PNPDeviceID[i_str_VID+4:i_str_VID+8]
            i_str_PID = str_PNPDeviceID.find("PID")
            str_PID = str_PNPDeviceID[i_str_PID+4:i_str_PID+8]
            str_COM = serialPorts.DeviceID
            str_COM = str_COM[3:]
            #from IPython import embed; embed()
            if (str_VID == "16C0" and str_PID == "0483"):  # Teensyduino
                # This is not very clean: it means going through the Serial
                # ports again, but using another function
                for i in range(0, len(ports)):   # for each Serial port
                    string_port = ports[i][3:]  # compare the string (we eliminate the "COM" part... not really needed)
                    #from IPython import embed; embed()
                    if string_port == str_COM:
                        tpt_ser.ser_micro = serial.Serial(ports[i])  # open Teensy serial port     
                        tpt_ser.portIndex_micro = i
                        tpt_settings.teensyConnected = True   
                        print("Connected to Teensyduino on port", string_port)
                        break
            elif (str_VID == "232E" and str_PID == "001C"):  # PSU
                str_SN = str_PNPDeviceID[22:]  # Serial Number of PSU
                print("PSU detected with Serial Number", str_SN)
                #if(str_SN == "2282020001"):  # High-Side PSU
                if(str_SN == "2512870010"):  # High-Side PSU
                    for i in range(0, len(ports)):   # for each Serial port
                        string_port = ports[i][3:]  # compare the string (we eliminate the "COM" part... not really needed)
                        #from IPython import embed; embed()
                        if string_port == str_COM:
                            tpt_ser.ser_PSU_HIGH = serial.Serial(ports[i])  # open PSU-HIGH serial port     
                            tpt_settings.psuHighConnected = True   
                            print("Connected to PSU HIGH on port", string_port)
                            print(tpt_ser.ser_PSU_HIGH)
                            break
                #elif(str_SN == "2131160002"):  # Low-Side PSU
                elif (str_SN == "2512870004"):  # Low-Side PSU
                    for i in range(0, len(ports)):   # for each Serial port
                        string_port = ports[i][3:]  # compare the string (we eliminate the "COM" part... not really needed)
                        #from IPython import embed; embed()
                        if string_port == str_COM:
                            tpt_ser.ser_PSU_LOW = serial.Serial(ports[i])  # open PSU-HIGH serial port     
                            tpt_settings.psuLowConnected = True   
                            print("Connected to PSU LOW on port", string_port)
                            break

    #                       = PICOSCOPE = where is pico scope ???
    # Create chandle and status ready for use
    chandle = ctypes.c_int16()
    status = {}
    str_model = ""
    tpt_settings.picoConnected = False
    pico_series = 0  # pico model series (2 = 2000a, 3 = 3000a, etc.)
    
    # Try to open PicoScope 2000 Series device
    # Returns handle to chandle for use in future API functions
    status["openunit"] = ps2.ps2000aOpenUnit(ctypes.byref(chandle), None)

    try:
        assert_pico_ok(status["openunit"])
        tpt_settings.picoConnected = True
        pico_series = 2
        
        str_model_bin = b"                "
        str_length = ctypes.c_int16(0)
        
        status["deviceinfo"] = ps2.ps2000aGetUnitInfo(chandle,
                                                      ctypes.c_char_p(str_model_bin),
                                                      len(str_model_bin),
                                                      ctypes.byref(str_length),
                                                      ps2.PICO_INFO['PICO_VARIANT_INFO'])
        assert_pico_ok(status["deviceinfo"])

        str_model = str_model_bin.decode('utf-8').replace(" ", "").replace("\x00", "")
        print("-----------------------------------------------")
        print("PicoScope 2000 series connected - Model: " + str_model)
        print("-----------------------------------------------")

    except PicoSDKCtypesError:
        print("-----------------------------------------------")
        print("PicoScope 2000a not found.")
    
    if (tpt_settings.picoConnected is False):
        # Try to open PicoScope 3000 Series device
        # Returns handle to chandle for use in future API functions
        status["openunit"] = ps3.ps3000aOpenUnit(ctypes.byref(chandle), None)

        try:
            assert_pico_ok(status["openunit"])
            tpt_settings.picoConnected = True
            str_model_bin = b"                "
            str_length = ctypes.c_int16(0)
        
            status["deviceinfo"] = ps3.ps3000aGetUnitInfo(chandle,
                                                          ctypes.c_char_p(str_model_bin),
                                                          len(str_model_bin),
                                                          ctypes.byref(str_length),
                                                          ps3.PICO_INFO['PICO_VARIANT_INFO'])
            assert_pico_ok(status["deviceinfo"])

            str_model = str_model_bin.decode('utf-8').replace(" ", "").replace("\x00", "")
            print("-----------------------------------------------")
            print("PicoScope 3000 series connected - Model: " + str_model)
            print("-----------------------------------------------")

            pico_series = 3

        except PicoSDKCtypesError:
            print("-----------------------------------------------")
            print("PicoScope 3000a not found.")
            print("-----------------------------------------------")        
    #from IPython import embed; embed()

    # Manage Power Supplies
    if(not tpt_settings.psuHighConnected and tpt_settings.psuLowConnected):
        print("WARNING - only PSU LOW is connected!")
        print("Disabling PSU HIGH...")
        tpt_ser.ser_PSU_HIGH.close()
        tpt_settings.psuHighConnected = False
    elif(tpt_settings.psuHighConnected and not tpt_settings.psuLowConnected):
        print("WARNING - only PSU HIGH is connected!")
        print("Disabling PSU LOW...")
        tpt_ser.ser_PSU_LOW.close()
        tpt_settings.psuLowConnected = False
    elif(tpt_settings.psuHighConnected and tpt_settings.psuLowConnected):
        tpt_settings.psusConnected = True
        # enable remote mode, set limits, etc.
        #PSU HIGH
        print("PSU HIGH ping data:")
        str_PSU_HIGH_ping = tpt_PSU.get_PSU_ping_data(tpt_ser.ser_PSU_HIGH)
        print(str_PSU_HIGH_ping)
        print("Enabling remote control on PSU HIGH...")
        aux_str = "SYST:LOCK ON"
        tpt_ser.ser_PSU_HIGH.write(aux_str.encode('UTF-8'))
        # TODO check that the remote control is actually enabled
        print("Remote control on PSU HIGH enabled")
        print("Setting limits and protections on PSU HIGH...")
        tpt_PSU.set_PSU_protections(tpt_ser.ser_PSU_HIGH)
        tpt_PSU.disable_PSU(tpt_ser.ser_PSU_HIGH)
        tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_HIGH, 0)

        print("")
        print("PSU LOW ping data:")
        str_PSU_LOW_ping = tpt_PSU.get_PSU_ping_data(tpt_ser.ser_PSU_LOW)
        print(str_PSU_LOW_ping)
        print("Enabling remote control on PSU LOW...")
        aux_str = "SYST:LOCK ON"
        tpt_ser.ser_PSU_LOW.write(aux_str.encode('UTF-8'))
        # TODO check that the remote control is actually enabled
        print("Remote control on PSU LOW enabled")
        print("Setting limits and protections on PSU LOW...")
        tpt_PSU.set_PSU_protections(tpt_ser.ser_PSU_LOW)
        tpt_PSU.disable_PSU(tpt_ser.ser_PSU_LOW)
        tpt_PSU.set_PSU_voltage_modbus(tpt_ser.ser_PSU_LOW, 0)
        
        # testing PSU
        #tpt_PSU.disable_PSU(ser_PSU_HIGH)
        #tpt_PSU.set_PSU_voltage_modbus(ser_PSU_HIGH, 2.556)
        #tpt_PSU.enable_PSU(ser_PSU_HIGH)
        #tpt_PSU.disable_PSU(ser_PSU_HIGH)
        
        #status_PSU, stored_voltage = tpt_PSU.set_PSU_voltage(ser_PSU_HIGH, 2.556)
        #for millivolt in range(0, 1000, 5):
        #    tpt_PSU.set_PSU_voltage_modbus(ser_PSU_HIGH, 0.001 * millivolt)
        #    time.sleep(0.5)
        #for millivolt in range(1000, 0, -5):
        #    tpt_PSU.set_PSU_voltage_modbus(ser_PSU_HIGH, 0.001 * millivolt)
        #    time.sleep(0.5)
        #from IPython import embed; embed()
        
    input("\nPress any key to continue.")
    clear_terminal()
    # Loop over - User Interface

    while inProgram:
        print_main_menu()
        answer = input("Your selection: ")
        if answer == "1":       # Modify microcontroller parameters
            if tpt_settings.teensyConnected:
                inSubmenu = True
                while(inSubmenu):
                    inSubmenu = submenu_modify_microcontroller_parameters()
            else:
                print("Microcontroller not connected - Option unavailable")
                input("\nPress any key to continue.")
                clear_terminal()
        elif answer == "2":     # Show the memory contents of the microcontroller
            if tpt_settings.teensyConnected:
                # Serial listener
                # https://stackoverflow.com/questions/26047544/python-serial-port-listener
                # https://makersportal.com/blog/2018/2/25/python-datalogger-reading-the-serial-output-from-arduino-to-analyze-data-using-pyserial
                tpt_micro.get_microcontroller_memory_contents(True)
                input("\nPress any key to continue.")
                clear_terminal()
            else:
                print("Microcontroller not connected - Option unavailable")
                input("\nPress any key to continue.")
                clear_terminal()
        elif answer == "3":     # Launch a TPT test with the stored parameters
            if tpt_settings.teensyConnected:
                tpt_micro.execute_TPT_microcontroller()
                clear_terminal()
            else:
                print("Microcontroller not connected - Option unavailable")
                input("\nPress any key to continue.")
                clear_terminal()
        elif answer == "4":     # Perform an inductance test with the stored parameters
            if tpt_settings.teensyConnected:
                tpt_micro.execute_Inductance_Test_microcontroller()
                clear_terminal()
            else:
                print("Microcontroller not connected - Option unavailable")
                input("\nPress any key to continue.")
                clear_terminal()
        elif answer == "5":
            if tpt_settings.picoConnected and tpt_settings.teensyConnected:
                temporary_var = tpt_automation.inductance_guided_test(chandle, pico_series)
                clear_terminal()
            else:
                print("PicoScope or microcontroller not connected - Option unavailable")
                input("\nPress any key to continue.")
                clear_terminal()
        elif answer == "6":
            if tpt_settings.psusConnected and tpt_settings.teensyConnected:
                if(tpt_settings.saturation_calculated):
                    tpt_automation.demagnetize_core()
                    print("Demagnetization process completed.")
                    input("\nPress any key to continue.")
                    clear_terminal()                           
                else:
                    print("Please execute inductance test first.")
                    input("\nPress any key to continue.")
                    clear_terminal()
            else:
                print("Programmable PSUs or microcontroller not connected - Option unavailable")
                input("\nPress any key to continue.")
                clear_terminal()    
        elif answer == "7":
            if tpt_settings.teensyConnected:
                inductance_test_show()
                input("\nPress any key to continue.")
                clear_terminal()
            else:
                print("Microcontroller not connected - Option unavailable")
                input("\nPress any key to continue.")
                clear_terminal()
        elif answer == "8":     # Reset microcontroller
            if tpt_settings.teensyConnected:
                tpt_micro.reset_microcontroller()
                input("\nPress any key to continue.")
                clear_terminal()
            else:
                print("Microcontroller not connected - Option unavailable")
                input("\nPress any key to continue.")
                clear_terminal()
        elif answer == "9":     # guided TPT test
            # picoConnected = True  # UNCOMMENT FOR DEBUG WITHOUT HARDWARE
            # saturation_calculated = True  # UNCOMMENT FOR DEBUG WITHOUT HARDWARE
            # ser_micro = []  # UNCOMMENT FOR DEBUG WITHOUT HARDWARE
            if tpt_settings.picoConnected and tpt_settings.teensyConnected:
                #if(tpt_settings.saturation_calculated):
                    clear_terminal()
                    temporary_var = tpt_automation.TPT_guided_test(chandle, pico_series)
                    input("\nPress any key to continue.")
                    clear_terminal()
                #else:
                #    print("Please execute inductance test first.")
                #    input("\nPress any key to continue.")
                #    clear_terminal()
            else:
                print("PicoScope or microcontroller not connected - Option unavailable")
                input("\nPress any key to continue.")
                clear_terminal()
            #from IPython import embed; embed()
        elif answer == "10":  # test interpolation
            print("TEMPORARY SCRIPT - DEBUG FOR OVERSAMPLING/DESKEWING")
            n_samples = 40
            timestep = 32  # ns
            start_time = 10  # ns
            end_time = start_time + timestep * (n_samples - 1)
            #original_measurement_array = np.linspace(-3, 5, n_samples)
            original_measurement_array = np.random.rand(n_samples)
            original_time_array = np.linspace(start_time, end_time, n_samples)
            new_sample_time = 0.1  # ns
            skew_ns = 5
            interpolator = 1  # hold:0 / linear:1
            tpt_aux.interpolate_data(original_measurement_array, original_time_array, new_sample_time, skew_ns, interpolator)
            clear_terminal()
        elif answer == "11":  # process existing TPT test
            tpt_calculations.TPT_guided_processing()
            input("\nPress any key to continue.")
            clear_terminal()

        elif answer == "12":  #
            tpt_automation.TPT_group_variables_setup()
            input("\nPress any key to continue.")
            clear_terminal()

        elif answer == "0":    
            inProgram = False
        else:
            print("Unrecognized command. Please select an option from the list.")
            input("\nPress any key to continue.")
            clear_terminal()
    # ser.write(b'Z')     # write a string
    # send_commands_TPT(ser, 1, 1234, 567, 891, 9876, 4)
    # from IPython import embed; embed()
    if(tpt_settings.teensyConnected):    # close serial port
        tpt_ser.ser_micro.close()             
    if(tpt_settings.psuHighConnected and tpt_settings.psuLowConnected):
        tpt_ser.ser_PSU_HIGH.close()
        tpt_ser.ser_PSU_LOW.close()
    if(tpt_settings.picoConnected):      # close picoScope
        status["close"] = ps2.ps2000aCloseUnit(chandle)
        assert_pico_ok(status["close"])


def magnetic_to_electrical(B_offset, B_delta, frequency, Ae, Np, V_diode, V_transistor):
    if B_offset >= 0:
        T1 = (B_offset + B_delta)/(4 * frequency * B_delta)
        T2 = 1/(2 * frequency)
        V1 = 4 * Ae * Np * frequency * B_delta - V_diode
        V2 = V1 - V_transistor - V_diode
    else:
        T1 = (B_delta - B_offset)/(4 * frequency * B_delta)
        T2 = 1/(2 * frequency)
        V2 = 4 * Ae * Np * frequency * B_delta + V_transistor 
        V1 = V2 - V_transistor - V_diode
    return T1, T2, V1, V2


def clear_terminal():
    os.system('cls' if os.name == 'nt' else 'clear')


def inductance_test_show():
    tpt_micro.modify_microcontroller_parameter("S", 0)
    for t in range(50, 2000, 50):
        tpt_micro.modify_microcontroller_parameter("TL", t)
        time.sleep(0.1)
        tpt_micro.execute_Inductance_Test_microcontroller()
        time.sleep(0.1)
    for t in range(1950, 50, -50):
        tpt_micro.modify_microcontroller_parameter("TL", t)
        time.sleep(0.1)
        tpt_micro.execute_Inductance_Test_microcontroller()
        time.sleep(0.1)
    tpt_micro.modify_microcontroller_parameter("S", 1)
    for t in range(50, 2000, 50):
        tpt_micro.modify_microcontroller_parameter("TL", t)
        time.sleep(0.1)
        tpt_micro.execute_Inductance_Test_microcontroller()
        time.sleep(0.1)
    for t in range(1950, 50, -50):
        tpt_micro.modify_microcontroller_parameter("TL", t)
        time.sleep(0.1)
        tpt_micro.execute_Inductance_Test_microcontroller()
        time.sleep(0.1)


def print_main_menu():
    print("+---------------------------------------------------+")
    print("|  Welcome to the Magnetics Characterization Suite  |")
    print("+---------------------------------------------------+")
    print("Choose an option to continue:")
    print("1 - Manually define a microcontroller parameter")
    print("2 - Show the memory contents of the microcontroller")
    print("3 - Perform a TPT test with the stored parameters")
    print("4 - Launch an inductance test with the stored parameters")
    print("5 - Perform a guided inductance test")
    print("6 - Demagnetize core")
    print("7 - Inductance Test Show")
    print("8 - Reset Microcontroller")
    print("9 - Perform a guided TPT test")
    print("10 - Debug script for oversampling/deskewing")
    print("11 - Process existing TPT test")
    print("12 - Input TPT variable group")
    print("0 - Exit")
    print("-----------------------------------------------")


def submenu_modify_microcontroller_parameters():
    inSubmenu = True
    clear_terminal()
    print("+---------------------------------------------+")
    print("|          Microcontroller parameters         |")
    print("+---------------------------------------------+")
    print("Please select a parameter to modify:")
    print("1 - T1 [TPT]")
    print("2 - T2A [TPT]")
    print("3 - T2B [TPT]")
    print("4 - T3 [TPT]")
    print("5 - TL [Inductance Test]")
    print("6 - N [TPT]")
    print("7 - S (Sign) [TPT, Inductance Test, Demagnetization]")
    print("8 - CORE_DEMAG_SCHEDULED [Demagnetization]")
    print("9 - Go back")
    print("-----------------------------------------------")
    answer2 = input("Your selection: ")
    print("-----------------------------------------------")
    if answer2 == "1":
        input_value = int(input("Define T1 in nanoseconds: "))
        write_ok = tpt_micro.modify_microcontroller_parameter("T1", input_value)
        if(write_ok):
            print("Sent command: 'A," + str(input_value) + "'")
            input("\nPress any key to continue.")
        else:
            print("T1 must be equal or larger than zero.")
            input("\nPress any key to continue.")
    elif answer2 == "2":
        input_value = int(input("Define T2A in nanoseconds: "))
        write_ok = tpt_micro.modify_microcontroller_parameter("T2A", input_value)
        if(write_ok):
            print("Sent command: 'B," + str(input_value) + "'")
            input("\nPress any key to continue.")
        else:
            print("T2A must be equal or larger than 50 ns.")
            input("\nPress any key to continue.")
    elif answer2 == "3":
        input_value = int(input("Define T2B in nanoseconds: "))
        write_ok = tpt_micro.modify_microcontroller_parameter("T2B", input_value)
        if(write_ok):
            print("Sent command: 'C," + str(input_value) + "'")
            input("\nPress any key to continue.")
        else:
            print("T2B must be equal or larger than 50 ns.")
            input("\nPress any key to continue.")   
    elif answer2 == "4":
        input_value = int(input("Define T3 in nanoseconds: "))
        write_ok = tpt_micro.modify_microcontroller_parameter("T3", input_value)
        if(write_ok):
            print("Sent command: 'D," + str(input_value) + "'")
            input("\nPress any key to continue.")
        else:
            print("T3 must be equal or larger than zero.")
            input("\nPress any key to continue.")
    elif answer2 == "5":
        input_value = int(input("Define TL in nanoseconds: "))
        write_ok = tpt_micro.modify_microcontroller_parameter("TL", input_value)
        if(write_ok):
            print("Sent command: 'E," + str(input_value) + "'")
            input("\nPress any key to continue.")
        else:
            print("T3 must be equal or larger than zero.")
            input("\nPress any key to continue.")
    elif answer2 == "6":
        input_value = int(input("Define N: "))
        write_ok = tpt_micro.modify_microcontroller_parameter("N", input_value)
        if(write_ok):
            print("Sent command: 'F," + str(input_value) + "'")
            input("\nPress any key to continue.")
        else:
            print("N must be between 2 and 255.")
            time.sleep(2)
    elif answer2 == "7":
        input_value = int(input("Define the sign of the initial current (0: POS / 1: NEG): "))
        write_ok = tpt_micro.modify_microcontroller_parameter("T1", input_value)
        if(write_ok):
            print("Sent command: 'G," + str(input_value) + "'")
            input("\nPress any key to continue.")
        else:
            print("S must be 0 (positive) or 1 (negative).")
            input("\nPress any key to continue.")
    elif answer2 == "8":
        input_value = int(input("Define CORE_DEMAG_SCHEDULED (0: NOT SCHEDULED / 1: SCHEDULED): "))
        write_ok = tpt_micro.modify_microcontroller_parameter("CORE_DEMAG", input_value)
        if(write_ok):
            print("Sent command: 'H," + str(input_value) + "'")
            input("\nPress any key to continue.")
        else:
            print("CORE_DEMAG_SCHEDULED must be 0 (not scheduled) or 1 (scheduled).")
            input("\nPress any key to continue.")
    elif answer2 == "9":
        inSubmenu = False
        clear_terminal()
    else:
        print("Unrecognized command. Please select an option from the list.")
        input("\nPress any key to continue.")
        clear_terminal()
    return inSubmenu


#def TPT_magnetic_parameters_to_test_parameters
    # there is a funtion called "magnetic_to_electrical" that should be absorbed here...


if __name__ == "__main__":
    Init()
    print('done')
    # time.sleep(2)  