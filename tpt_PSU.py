import serial
import time

def get_PSU_serial_message(ser, print_content):
    read_ok = False
    ser_str = "Empty"
    time.sleep(0.1)
    number_read_lines = 0
    previous_time = time.time()
    timeout = False
    while(not timeout):
        if(ser.in_waiting > 1):
            ser_bytes = ser.readline()
            number_read_lines = number_read_lines + 1
            ser_str = str(ser_bytes)
            ser_str = str(ser_bytes)[2:-3]
            if(print_content):
                print(ser_str)
            previous_time = time.time()
        elapsed_time = time.time() - previous_time
        if (elapsed_time > 2):  # timeout
            timeout = True
    read_ok = True
    return read_ok, ser_str


def get_PSU_ping_data(ser_PSU):
    ser_PSU.reset_input_buffer()    # flush serial input buffer
    aux_str = "*IDN?"
    ser_PSU.write(aux_str.encode('UTF-8'))
    read_ok, str_PSU_ping = get_PSU_serial_message(ser_PSU, False)
    if (not read_ok):
        print("PING message was not processed correctly.")
    return str_PSU_ping


def set_PSU_voltage_SCPI(ser_PSU, volt):
    #sets the defined voltage value on PSU (ser_PSU), and checks and returns the stored value
    # status: 0 - error / 1 - ok (I can develop this to take into account different errors)
    # This method is significantly slower than MODBUS
    
    #define voltage
    volt = round(volt, 3)
    aux_str = "VOLT " + str(volt)
    ser_PSU.write(aux_str.encode('UTF-8'))

    #get voltage
    ser_PSU.reset_input_buffer()
    time.sleep(0.1)
    #aux_str = "MEAS:VOLT?"
    aux_str = "VOLT?\n"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)
    _, message_voltage = get_PSU_serial_message(ser_PSU, False)
    print(message_voltage)
    #from IPython import embed; embed()
    stored_voltage = float(message_voltage[0:-3])
    if(stored_voltage == volt):
        status = 1
    else:
        status = 0
    return status, stored_voltage


def get_PSU_stored_voltage(ser_PSU):
    ser_PSU.reset_input_buffer()
    time.sleep(0.1)
    aux_str = "VOLT?\n"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)
    _, message_voltage = get_PSU_serial_message(ser_PSU, False)
    stored_voltage = float(message_voltage)
    return stored_voltage


def get_PSU_measured_voltage(ser_PSU):
    ser_PSU.reset_input_buffer()
    time.sleep(0.1)
    aux_str = "MEAS:VOLT?\n"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)
    _, message_voltage = get_PSU_serial_message(ser_PSU, False)
    measured_voltage = float(message_voltage[0:-2])
    return measured_voltage


def enable_PSU(ser_PSU):
    aux_str = "OUTP ON\n"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(0.1)


def disable_PSU(ser_PSU):
    aux_str = "OUTP OFF"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(1)


def set_PSU_protections(ser_PSU):

    # define OCP (overcurrent protection): 2 A
    aux_str = "CURR:PROT 2\n"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)
    # not sure why it isn't accepted on the first attempt
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)

    # define OVP (overvoltage protection): 410 volt
    aux_str = "VOLT:PROT 410\n"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(2)

    # define current range: 0 - 1 A
    aux_str = "CURR:LIM:LOW 0.0\n"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)
    aux_str = "CURR:LIM:HIGH 1.0\n"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)

    # define voltage range: 0 - 400 A
    aux_str = "VOLT:LIM:LOW 0.0\n"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)
    aux_str = "VOLT:LIM:HIGH 400.0\n"
    ser_PSU.write(aux_str.encode('UTF-8'))
    time.sleep(0.5)
    #from IPython import embed; embed()


def generate_CRC_byte(initial_CRC, input_byte):    
    # initial_CRC: 2 bytes
    # input_byte: 1 byte
    debug = 0
    new_CRC = initial_CRC ^ input_byte
    if(debug):
        print("XOR first character:", hex(new_CRC), " - ", format(new_CRC, '#018b'))
    N = 1  # index 1 to reflect the documentation (easier to follow)
    while(N <= 8):
        # get value of LSB and move CRC to the right
        LSB = new_CRC % 2
        new_CRC = new_CRC >> 1
        if(debug):
            print("move", N, " - ", format(new_CRC, '#018b'))
        if(LSB):  # carry over
            new_CRC = new_CRC ^ 0xA001  # polynomial value
            if(debug):
                print("Applied XOR POLY:", format(new_CRC, '#018b'))
        N = N + 1
    return new_CRC


def set_PSU_voltage_modbus(ser_PSU, voltage):
    debug = 0
    voltage = round(voltage, 3)

    if(debug):
        nominal_voltage = 750
    else:
        ser_PSU.reset_input_buffer()
        time.sleep(0.1)
        aux_str = "SYST:NOM:VOLT?\n"
        ser_PSU.write(aux_str.encode('UTF-8'))
        time.sleep(0.5)
        _, message_voltage = get_PSU_serial_message(ser_PSU, False)
        nominal_voltage = float(message_voltage[0:-3])
    
    # calculate data - not really a percentage, but the doc says so
    per_cent_voltage = max(min(round(52428 * voltage / nominal_voltage), 52428), 0)
    if(debug):
        print("Per_cent_voltage:", per_cent_voltage, "HEX:", hex(per_cent_voltage))
    # build message
    byte_array = [None] * 8
    byte_array[0] = 0x00  # MODBUS message
    byte_array[1] = 0x06  # function WRITE single register
    #bytes23 = 0x01F4  # Modbus_Register_PS9000_KE3.05+_EN.pdf - Set voltage value
    byte_array[2] = 0x01
    byte_array[3] = 0xF4
    #bytes45 = hex(per_cent_voltage)  # data word - do I really need to explicitly declare it is HEX?
    byte_array[4] = per_cent_voltage >> 8
    byte_array[5] = per_cent_voltage - (byte_array[4] << 8)

    # calculate CRC
    CRC = 0xFFFF
    for i in range(0, 6):
        CRC = generate_CRC_byte(CRC, byte_array[i])
    if(debug):
        print("CRC: ", format(CRC, '#018b'), " = ", hex(CRC))
    byte_array[7] = CRC >> 8  # higher byte
    byte_array[6] = CRC - (byte_array[7] << 8) # lower byte

    # send message
    ser_PSU.write(byte_array)
    time.sleep(0.4)
    
    if(debug):
        print("byte0: ", hex(byte_array[0]))
        print("byte1: ", hex(byte_array[1]))
        print("byte2: ", hex(byte_array[2]))
        print("byte3: ", hex(byte_array[3]))
        print("byte4: ", hex(byte_array[4]))
        print("byte5: ", hex(byte_array[5]))
        print("byte6: ", hex(byte_array[6]))
        print("byte7: ", hex(byte_array[7]))
        aux_str = ""
        for i in range(0, 8):
            aux_str = aux_str + chr(byte_array[i])
        print(aux_str)
