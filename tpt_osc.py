from picosdk.ps2000a import ps2000a as ps2
from picosdk.ps3000a import ps3000a as ps3
from picosdk.functions import adc2mV, mV2adc, assert_pico_ok
from picosdk.constants import PICO_STATUS, PICO_STATUS_LOOKUP
from picosdk.errors import PicoSDKCtypesError
import ctypes
import numpy as np
import math


def get_str_from_oscilloscope_status(status_code):
    key_list = list(ps2.PICO_STATUS.keys())
    val_list = list(ps2.PICO_STATUS.values())

    position = val_list.index(status_code)
    status_str = key_list[position]

    return status_str


def oscilloscope_range_str2code(str_channel: object, pico_series: object) -> object:
    if (pico_series == 2):
        string_aux = 'PS2000A_CHANNEL_' + str_channel
        channel_code = ps2.PS2000A_CHANNEL[string_aux]
    elif (pico_series == 3):
        string_aux = 'PS3000A_CHANNEL_' + str_channel
        channel_code = ps3.PS3000A_CHANNEL[string_aux]
    return channel_code


def oscilloscope_range_code2str(code_channel, pico_series):
    if (pico_series == 2 or pico_series == 3):
        if (code_channel == 0):
            channel_str = "A"
        elif (code_channel == 1):
            channel_str = "B"
        elif (code_channel == 2):
            channel_str = "C"
        elif (code_channel == 3):
            channel_str = "D"
        else:
            channel_str = "NO CHANNEL"
    return channel_str


def set_oscilloscope_channel_range(chandle, channel, channel_range, analogue_offset, pico_series):
    enabled = 1
    if (pico_series == 2):
        status = ps2.ps2000aSetChannel(chandle,
                                       channel,
                                       enabled,
                                       ps2.PS2000A_COUPLING['PS2000A_DC'],
                                       channel_range,
                                       analogue_offset)
    elif (pico_series == 3):
        status = ps3.ps3000aSetChannel(chandle,
                                       channel,
                                       enabled,
                                       ps3.PS3000A_COUPLING['PS3000A_DC'],
                                       channel_range,
                                       analogue_offset)

        #ps3.ps3000aSetBandwidthFilter(chandle,
        #                              channel,
         #                             ps3.PS3000A_BANDWIDTH_LIMITER['PS3000A_BW_20MHZ'])
    try:
        assert_pico_ok(status)
    except PicoSDKCtypesError:
        print("Problem setting oscilloscope range in channel ", oscilloscope_range_code2str(channel, pico_series))


def disable_oscilloscope_channel(chandle, channel, pico_series):
    disabled = 0
    if (pico_series == 2):
        status = ps2.ps2000aSetChannel(chandle,
                                       channel,
                                       disabled,
                                       ps2.PS2000A_COUPLING['PS2000A_DC'],
                                       ps2.PS2000A_RANGE['PS2000A_20V'],
                                       0.0)
    elif (pico_series == 3):
        status = ps3.ps3000aSetChannel(chandle,
                                       channel,
                                       disabled,
                                       ps3.PS3000A_COUPLING['PS3000A_DC'],
                                       ps3.PS3000A_RANGE['PS3000A_20V'],
                                       0.0)
    try:
        assert_pico_ok(status)
    except PicoSDKCtypesError:
        print("Problem disabling channel ", oscilloscope_range_code2str(channel, pico_series))


def get_oscilloscope_input_range(max_voltage, pico_series):
    # Definition of oscilloscope input ranges
    # Last value listed in the library (50 V) is omitted because it does not comply
    # with the specs of the device 
    ps2000a_input_ranges = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20]  # volt; 0.01 not allowed
    ps3000a_input_ranges = [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20]  # volt; 0.01 not allowed

    if(pico_series == 2):
        n_range = 0
        if(max_voltage > ps2000a_input_ranges[-1]):
            withinRange = False
        else:
            withinRange = True
            for n_range in range(1, len(ps2000a_input_ranges)):     # begin in index 1 (0.02 V) because 0.01 V is not allowed for ps2000a
                if (max_voltage <= ps2000a_input_ranges[n_range]):
                    break
            # n_range = n_range + 1
    elif(pico_series == 3):
        n_range = 0
        if(max_voltage > ps3000a_input_ranges[-1]):
            withinRange = False
        else:
            withinRange = True
            for n_range in range(1, len(ps3000a_input_ranges)):     # begin in index 1 (0.02 V) because 0.01 V is not allowed for ps3000a
                if (max_voltage <= ps3000a_input_ranges[n_range]):
                    break
            # n_range = n_range + 1
    return n_range, withinRange


def print_channel_range(channel_str, channel_range, pico_series):
    if (pico_series == 2):
        print("Channel ", channel_str, " range: ", list(ps2.PS2000A_RANGE)[channel_range])
    elif (pico_series == 3):
        print("Channel ", channel_str, " range: ", list(ps3.PS3000A_RANGE)[channel_range])


def check_oscilloscope_maximum_value(chandle, maxADC, pico_series):
    if (pico_series == 2):
        status = ps2.ps2000aMaximumValue(chandle, ctypes.byref(maxADC))
    elif (pico_series == 3):
        status = ps3.ps3000aMaximumValue(chandle, ctypes.byref(maxADC))
    assert_pico_ok(status)


def set_oscilloscope_trigger(chandle, channel_code, trigger_threshold_counts, threshold_direction, pico_series):
    enabled = 1
    delay = 0
    timeout = 5000
    #threshold_direction: "RISING" or "FALLING"
    if (pico_series == 2):
        if (threshold_direction == "RISING"):
            threshold = ps2.PS2000A_THRESHOLD_DIRECTION['PS2000A_RISING']
        elif (threshold_direction == "FALLING"):
            threshold = ps2.PS2000A_THRESHOLD_DIRECTION['PS2000A_FALLING']
        status = ps2.ps2000aSetSimpleTrigger(chandle, 
                                             enabled,
                                             channel_code,
                                             trigger_threshold_counts,
                                             threshold,
                                             delay,
                                             timeout)
    elif (pico_series == 3):
        if (threshold_direction == "RISING"):
            threshold = ps3.PS3000A_THRESHOLD_DIRECTION['PS3000A_RISING']
        elif (threshold_direction == "FALLING"):
            threshold = ps3.PS3000A_THRESHOLD_DIRECTION['PS3000A_FALLING']
        status = ps3.ps3000aSetSimpleTrigger(chandle, 
                                             enabled,
                                             channel_code,
                                             trigger_threshold_counts,
                                             threshold,
                                             delay,
                                             timeout)
    assert_pico_ok(status)


def determine_if_trigger_occurred(data_array, trigger_level):
    if(np.max(data_array) >= trigger_level):
        trigger_occurred = True
    else:
        trigger_occurred = False

    return trigger_occurred


def run_oscilloscope_block_acquisition(chandle, actual_pre_trigger_samples, actual_post_trigger_samples, n_timebase, pico_series):
    if(pico_series == 2):
        # Run block capture
        # handle = chandle
        # number of pre-trigger samples = preTriggerSamples
        # number of post-trigger samples = PostTriggerSamples
        # timebase = 8 = 80 ns = timebase (see Programmer's guide for mre information on timebases)
        # oversample = 0 = oversample
        # time indisposed ms = None (not needed in the example)
        # segment index = 0
        # lpReady = None (using ps2000aIsReady rather than ps2000aBlockReady)
        # pParameter = None
        oversample = 0
        status = ps2.ps2000aRunBlock(chandle,
                                     actual_pre_trigger_samples,
                                     actual_post_trigger_samples,
                                     n_timebase,
                                     oversample,
                                     None,
                                     0,
                                     None,
                                     None)
    elif(pico_series == 3):
        # Run block capture
        # handle = chandle
        # number of pre-trigger samples = preTriggerSamples
        # number of post-trigger samples = PostTriggerSamples
        # timebase = 8 = 80 ns = timebase (see Programmer's guide for mre information on timebases)
        # oversample = 0 = oversample
        # time indisposed ms = None (not needed in the example)
        # segment index = 0
        # lpReady = None (using ps2000aIsReady rather than ps2000aBlockReady)
        # pParameter = None
        oversample = 0
        status = ps3.ps3000aRunBlock(chandle,
                                     actual_pre_trigger_samples,
                                     actual_post_trigger_samples,
                                     n_timebase,
                                     oversample,
                                     None,
                                     0,
                                     None,
                                     None)
    assert_pico_ok(status)


def wait_oscilloscope_acquisition_ready(chandle, pico_series):
    # Check for data collection to finish using ps2000aIsReady
    ready = ctypes.c_int16(0)
    check = ctypes.c_int16(0)
    if (pico_series == 2):
        while ready.value == check.value:
            status = ps2.ps2000aIsReady(chandle, ctypes.byref(ready))
    elif(pico_series == 3):
        while ready.value == check.value:
            status = ps3.ps3000aIsReady(chandle, ctypes.byref(ready))


def set_data_buffers(chandle, channel_code, buffer_max, total_samples, pico_series):
    # Set data buffer location for data collection from channels A and C
    # handle = chandle
    # source = PS2000A_CHANNEL_A / PS2000A_CHANNEL_
    # pointer to buffer max = ctypes.byref(...)
    # pointer to buffer min = None
    # buffer length = totalSamples
    # segment index = 0
    # ratio mode = PS2000A_RATIO_MODE_NONE = 0 / generic; no need to downsample
    if(pico_series == 2):
        status = ps2.ps2000aSetDataBuffers(chandle,
                                           channel_code,
                                           ctypes.byref(buffer_max),
                                           None,
                                           total_samples,
                                           0,
                                           ps2.PS2000A_RATIO_MODE['PS2000A_RATIO_MODE_NONE'])
    elif(pico_series == 3):
        status = ps3.ps3000aSetDataBuffers(chandle,
                                           channel_code,
                                           ctypes.byref(buffer_max),
                                           None,
                                           total_samples,
                                           0,
                                           ps3.PS3000A_RATIO_MODE['PS3000A_RATIO_MODE_NONE'])
    assert_pico_ok(status)


def get_oscilloscope_values(chandle, c_total_samples, overflow, pico_series):
    # Retrieved data from scope to assigned buffers
    # handle = chandle
    # start index = 0
    # pointer to number of samples = ctypes.byref(cTotalSamples)
    # downsample ratio = 0
    # downsample ratio mode = PS2000A_RATIO_MODE_NONE
    # pointer to overflow = ctypes.byref(overflow))
    if(pico_series == 2):        
        status = ps2.ps2000aGetValues(chandle,
                                      0,
                                      ctypes.byref(c_total_samples),
                                      0,
                                      0,
                                      ps2.PS2000A_RATIO_MODE['PS2000A_RATIO_MODE_NONE'],
                                      ctypes.byref(overflow))
    elif(pico_series == 3):
        status = ps3.ps3000aGetValues(chandle,
                                      0,
                                      ctypes.byref(c_total_samples),
                                      0,
                                      0,
                                      ps3.PS3000A_RATIO_MODE['PS3000A_RATIO_MODE_NONE'],
                                      ctypes.byref(overflow))
    assert_pico_ok(status)


def calculate_oscilloscope_time_config(chandle, desired_pre_trigger_samples, desired_post_trigger_samples, desired_sample_time, active_channels, pico_series):

    # PS200A - Calculate timebase timebase(n) - doc: page 28

    if (pico_series == 2):  # 500MS/s maximum sampling rate models
        if (desired_sample_time < 16):
            if(active_channels == 1 or active_channels == 2):
                n_timebase = max([1, math.floor(math.log2(0.5 * desired_sample_time))])  # do not consider n_timebase = 0; only for 1 channel operation
            elif(active_channels > 2):  # 3 or 4
                n_timebase = max([2, math.floor(math.log2(0.5 * desired_sample_time))])  # do not consider n_timebase = 0 nor 1
            else: # 1 active channel
                n_timebase = max([0, math.floor(math.log2(0.5 * desired_sample_time))])
        else:
            n_timebase = max([3, math.floor(0.0625 * desired_sample_time + 2)])

        #TODO DELATE THIS

        
        if(n_timebase <= 2):
            actual_sample_time = (2 ** n_timebase) / 0.5                    
        else:
            actual_sample_time = (n_timebase - 2) / 0.0625

        actual_post_trigger_samples = math.ceil(desired_post_trigger_samples * desired_sample_time / actual_sample_time)
        actual_pre_trigger_samples = math.ceil(desired_pre_trigger_samples * desired_sample_time / actual_sample_time)
        total_samples = actual_pre_trigger_samples + actual_post_trigger_samples
   
        # Assert status - API check  
        timeIntervalns = ctypes.c_float()
        returnedMaxSamples = ctypes.c_int32() 
        oversample = ctypes.c_int16(0)
        status = ps2.ps2000aGetTimebase2(chandle,
                                         n_timebase,
                                         total_samples,
                                         ctypes.byref(timeIntervalns),
                                         oversample,
                                         ctypes.byref(returnedMaxSamples),
                                         0)
    
    elif (pico_series == 3):  #3406D - PicoScope 3000D Series
        if (desired_sample_time < 8):
            if(active_channels > 1):
                #n_timebase = max([1, math.floor(math.log2(desired_sample_time))])  # do not consider n_timebase = 0; only for 1 channel operation
                n_timebase = max([1, math.floor(math.log2(desired_sample_time))])
            else:  # 1 active channel
                n_timebase = max([0, math.floor(math.log2(desired_sample_time))])
        else:
            #n_timebase = max([3, math.floor(0.0625 * desired_sample_time + 2)])
            n_timebase = max([3, math.floor(0.0625 * desired_sample_time + 2)])

        
        if(n_timebase <= 2):
            actual_sample_time = (2 ** n_timebase) / 0.5                    
        else:
            actual_sample_time = (n_timebase - 2) / 0.125

        #test
        #actual_sample_time = 8

        actual_post_trigger_samples = math.ceil(desired_post_trigger_samples * desired_sample_time / actual_sample_time)
        actual_pre_trigger_samples = math.ceil(desired_pre_trigger_samples * desired_sample_time / actual_sample_time)
        total_samples = actual_pre_trigger_samples + actual_post_trigger_samples
   
        # Assert status - API check  
        timeIntervalns = ctypes.c_float()
        returnedMaxSamples = ctypes.c_int32() 
        oversample = ctypes.c_int16(0)
        status = ps3.ps3000aGetTimebase2(chandle,
                                         n_timebase,
                                         total_samples,
                                         ctypes.byref(timeIntervalns),
                                         oversample,
                                         ctypes.byref(returnedMaxSamples),
                                         0)
    timebase_OK = False
    try:
        assert_pico_ok(status)
        if (total_samples <= returnedMaxSamples.value):
            timebase_OK = True
            print("The internal buffer can hold the requested number of samples.")
        else:
            print("Number of Samples: Not OK, buffer overflow")
    except PicoSDKCtypesError:
        from IPython import embed; embed()
        status_str = get_str_from_oscilloscope_status(status)
        print("\'getTimebase2\' status threw the following error: ", status_str)

    return timebase_OK, n_timebase, actual_sample_time, actual_pre_trigger_samples, actual_post_trigger_samples


def adc2mV_single_value(ADC_value, oscilloscope_range, maxADC, pico_series):
    # Like adc2mV, but with Python data type in ADC value
    if(pico_series == 2 or pico_series == 3):
        channelInputRanges = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000]
        vRange = channelInputRanges[oscilloscope_range]
        value_mV = ADC_value * vRange / maxADC.value

    return value_mV
    