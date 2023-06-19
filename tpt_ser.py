def init():
    global ser_PSU_HIGH  # Serial PSU High side
    global ser_PSU_LOW   # Serial PSU Low side
    global ser_micro     # Serial microcontroller / Teensy
    ser_micro = []

    global portIndex
    global portIndex_micro
    
    portIndex = 0
    portIndex_micro = 0
    