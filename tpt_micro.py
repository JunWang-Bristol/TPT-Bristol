import time
import serial  # pySerial, not Serial (important)
import sys
import glob
import tpt_ser

def get_microcontroller_memory_contents(print_content):
    read_ok = False
    # Order: T1, T2A, T2B, T3, TL, N, S, CORE_DEMAG_SCHEDULED, CORE_DEMAG_ENABLED
    stored_params = [None] * 9
    stored_params_status_ok = [False] * 9

    tpt_ser.ser_micro.reset_input_buffer()    # flush serial input buffer
    aux_str = "K\n"             # ask Teensy for its memory contents
    tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    time.sleep(0.1)
    
    print()
    number_read_lines = 0
    previous_time = time.time()
    timeout = False
    while(not timeout):
        if(tpt_ser.ser_micro.in_waiting > 1):
            ser_bytes = tpt_ser.ser_micro.readline()
            number_read_lines = number_read_lines + 1
            # Remove human-readable text
            ser_str = str(ser_bytes)[2:-5]
            processed_str = ser_str.replace(" ns - STATUS", "")
            processed_str = processed_str.replace(" cycles - STATUS", "")
            processed_str = processed_str.replace(" (POS) - STATUS", "").replace(" (NEG) - STATUS", "")
            processed_str = processed_str.replace(" (YES) - STATUS", "").replace(" (NO) - STATUS", "")
            processed_str = processed_str.replace(" (ENABLED) - STATUS", "").replace(" (NOT ENABLED) - STATUS", "")
            processed_str = processed_str.replace(": ", ",")
            if((number_read_lines >= 3) and (number_read_lines <= 11)):  # Lines with the memory data
                # print(processed_str)
                tokens = processed_str.split(",")
                # There is a bias (-3): the first lines are table headers
                #from IPython import embed; embed()
                stored_params[number_read_lines - 3] = int(tokens[1])
                if(tokens[2] == "OK" or tokens[2] == "N/A"):
                    stored_params_status_ok[number_read_lines - 3] = True
                #print(tokens)
                #
            if(print_content and (number_read_lines >= 2) and (number_read_lines <= 11)):
                print(ser_str)
            previous_time = time.time()
        elapsed_time = time.time() - previous_time
        if (elapsed_time > 2):  # timeout
            timeout = True
    if(number_read_lines == 12):
        read_ok = True
    #print(stored_params)
    #print(stored_params_status_ok)
    return read_ok, stored_params, stored_params_status_ok


def execute_TPT_microcontroller():
    aux_str = "T\n"
    tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)


def execute_Inductance_Test_microcontroller():
    aux_str = "L\n"
    tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)


def init_demagnetization_procedure_microcontroller():
    #from IPython import embed; embed()
    modify_microcontroller_parameter("CORE_DEMAG", 1)
    time.sleep(0.5)
    aux_str = "W\n"
    tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)


def stop_demagnetization_procedure_microcontroller():
    #from IPython import embed; embed()
    modify_microcontroller_parameter("CORE_DEMAG", 0)  # redundant, but better safe than sorry
    time.sleep(0.1)
    aux_str = "W\n"
    tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)


def reset_microcontroller():
    print("Sending reset command... Please wait.")
    aux_str = "Z\n"
    tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    time.sleep(5)
    tpt_ser.ser_micro.close()
    print("Reestablishing serial communication with microcontroller.")
    ports = serial_ports()
    string_port = ports[tpt_ser.portIndex_micro]
    tpt_ser.ser_micro = serial.Serial(string_port)  # open serial port
    print("Reconnected to serial port ", string_port)


def modify_microcontroller_parameter(parameter, value):
    param_ok = False
    if ((parameter == "T1") and (value >= 0)):
        param_ok = True
        aux_str = "A," + str(value) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    elif ((parameter == "T2A") and (value >= 50)):
        param_ok = True
        aux_str = "B," + str(value) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    elif ((parameter == "T2B") and (value >= 50)):
        param_ok = True
        aux_str = "C," + str(value) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    elif ((parameter == "T3") and (value >= 0)):
        param_ok = True
        aux_str = "D," + str(value) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    elif ((parameter == "TL") and (value >= 50)):
        param_ok = True
        aux_str = "E," + str(value) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    elif ((parameter == "N") and (value >= 2 and value <= 255)):
        param_ok = True
        aux_str = "F," + str(value) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    elif ((parameter == "S") and (value == 0 or value == 1)):
        param_ok = True
        aux_str = "G," + str(value) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    elif ((parameter == "CORE_DEMAG") and (value == 0 or value == 1)):
        param_ok = True
        aux_str = "H," + str(value) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
    time.sleep(0.3)
    return param_ok


def store_parameters_TPT(S, T1, T2A, T2B, T3, N):
    # ser: Serial object
    # S: 0 (positive) or 1 (negative)
    # T1, T2A, T2B, T3: in nanoseconds
    # N: between 2 and 255
    if (((S == 0) or (S == 1)) and (N > 1) and (N <= 255)):
        # A - T1
        aux_str = "A," + str(int(T1)) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
        time.sleep(0.5)
        # B - T2A
        aux_str = "B," + str(int(T2A)) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
        time.sleep(0.5)
        # C - T2B
        aux_str = "C," + str(int(T2B)) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
        time.sleep(0.5)
        # D - T3
        aux_str = "D," + str(int(T3)) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
        time.sleep(0.5)
        # F - N
        aux_str = "F," + str(int(N)) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
        time.sleep(0.5)
        # G - S
        aux_str = "G," + str(int(S)) + '\n'
        tpt_ser.ser_micro.write(aux_str.encode('UTF-8'))
        time.sleep(0.5)


def serial_ports():

    """ Lists serial port names
        https://stackoverflow.com/questions/12090503/listing-available-com-ports-with-python
        :raises EnvironmentError:
            On unsupported or unknown platforms
        :returns:
            A list of the serial ports available on the system
    """
    """
    使用的是串行端口，所有Teensy和PC的通信都是从port这个端口中走的，这里的具体内容有些不理解但不影响正常使用
    """
    if sys.platform.startswith('win'):
        ports = ['COM%s' % (i + 1) for i in range(256)]
    elif sys.platform.startswith('linux') or sys.platform.startswith('cygwin'):
        # this excludes your current terminal "/dev/tty"
        ports = glob.glob('/dev/tty[A-Za-z]*')
    elif sys.platform.startswith('darwin'):
        ports = glob.glob('/dev/tty.*')
    else:
        raise EnvironmentError('Unsupported platform')

    result = []
    for port in ports:
        try:
            s = serial.Serial(port)
            s.close()
            result.append(port)
        except (OSError, serial.SerialException):
            pass
    return result