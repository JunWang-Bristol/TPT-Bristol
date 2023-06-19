from PyQt5.QtWidgets import QApplication  # to get screen DPI
import sys
import datetime
import math
import numpy as np
import matplotlib.pyplot as plt


def get_monitor_DPI():
    # very buggy, crashes the program. Best solution: run the code offline once, then plug the result
    # into the constant variable at the top of the script
    app = QApplication(sys.argv)
    screen = app.screens()[0]
    dpi = screen.physicalDotsPerInch()
    app.quit()
    return dpi


def get_datetime_string():
    obj_datetime = datetime.datetime.now()
    str_datetime = str(obj_datetime.year) + "_" + str(obj_datetime.month).zfill(2) + "_" + \
        str(obj_datetime.day).zfill(2) + " - " + str(obj_datetime.hour).zfill(2) + "_" + \
        str(obj_datetime.minute).zfill(2) + "_" + str(obj_datetime.second).zfill(2)
    return str_datetime            


def prompt_question_yes_no(str_prompt, default_answer):
    # default answer: between brackets, Yes or No
    result = 2
    if(str_prompt[-1] != " "):  # add space
        str_prompt = str_prompt + " "
    if default_answer == "Y":
        str_prompt = str_prompt + "([Y]/N): "
    else:
        str_prompt = str_prompt + "(Y/[N]): "
    answer = input(str_prompt)
    if(answer == "Y" or answer == "y"):
        result = 1
    elif (answer == "N" or answer == "n"):
        result = 0
    elif (answer == ""):
        if default_answer == "Y":
            result = 1
        else:
            result = 0
    else:
        #from IPython import embed; embed()
        print("Please, type only Y for YES or N for NO.")
        result = prompt_question_yes_no(str_prompt[0:-8], default_answer)

    return result


def interpolate_data(original_measurement_array, original_time_array, new_sample_time, skew_ns, interpolator):
    # tip: calculations will be much easier if mod(skew_ns, new_sample_time) = 0 (it's its divisor)
    # because we can use a single timebase for all the oscilloscope channels

    # -------------------------------------- -------------------------------------- ---------------------------- #
    # TODO cover (or verify) the situation in which the timespan is not an exact multiple of the new_sample_time #
    # -------------------------------------- -------------------------------------- ---------------------------- #

    # interpolator: 0 - hold value / 1 - linear interpolation / 2 - ...etc
    # status: 0 - error / 1 - ok
    original_sample_time = original_time_array[1] - original_time_array[0]  # ns
    timespan = original_time_array[-1] - original_time_array[0]  # ns
    
    if(new_sample_time > original_sample_time):
        # downsampling - not implemented
        new_measurement_array = 0
        new_time_array = 0
        status = 0
    else:  # oversampling
        # linspace start / stop
        number_of_samples = math.floor(timespan / new_sample_time) + 1
        oversampled_time_array = np.linspace(original_time_array[0], original_time_array[-1], number_of_samples)
        oversampled_measurement_array = np.zeros(number_of_samples)
        #from IPython import embed; embed()
        if(interpolator == 0):  # hold value
            index_original_array = 0
            oversampled_measurement_array[0] = original_measurement_array[0]
            oversampled_measurement_array[-1] = original_measurement_array[-1]
            for index_oversampled_array in range(1, number_of_samples-1):  # to-do validate last position [stop]
                current_oversampled_time = oversampled_time_array[index_oversampled_array]
                original_time_pre = original_time_array[index_original_array]
                original_time_post = original_time_array[index_original_array + 1]
                if(original_time_post <= current_oversampled_time):  # we advance index_original_array / otherwise: hold previous value
                    index_original_array = index_original_array + 1
                oversampled_measurement_array[index_oversampled_array] = original_measurement_array[index_original_array]
            status = 1
            new_measurement_array = oversampled_measurement_array
            new_time_array = oversampled_time_array
        elif(interpolator == 1):  # linear interpolation
            #to be completed TODO
            index_original_array = 0
            oversampled_measurement_array[0] = original_measurement_array[0]
            oversampled_measurement_array[-1] = original_measurement_array[-1]
            for index_oversampled_array in range(1, number_of_samples-1):  # to-do validate last position [stop]
                current_oversampled_time = oversampled_time_array[index_oversampled_array]
                original_time_pre = original_time_array[index_original_array]
                original_time_post = original_time_array[index_original_array + 1]

                slope = (original_measurement_array[index_original_array + 1] - original_measurement_array[index_original_array]) / original_sample_time
                oversampled_measurement_array[index_oversampled_array] = original_measurement_array[index_original_array] + slope * (oversampled_time_array[index_oversampled_array] - original_time_pre)

                if(original_time_post <= current_oversampled_time):  # we advance index_original_array / otherwise: hold previous value
                    index_original_array = index_original_array + 1
                
                    # original_time_post - do something?
                #if (index_oversampled_array == 10):
                #from IPython import embed; embed()
            status = 1
            new_measurement_array = oversampled_measurement_array
            new_time_array = oversampled_time_array
        if(skew_ns != 0):
            # a positive value of skew_ns will delay the measurement, whereas a negative value will advance its time
            new_time_array = new_time_array + skew_ns

        # plot original and oversampled data
        plot_interpolation = False
        if(plot_interpolation):
            # plt.subplot(2, 1, 1)  # row 1, col 2 index 1
            # plt.plot(original_time_array, original_measurement_array, marker="o", color="blue")
            # plt.xlabel("Time - ns")
            # plt.tight_layout()
            # plt.ylabel('Original data')

            # plt.subplot(2, 1, 2)  # row 1, col 2 index 1
            # plt.plot(new_time_array, new_measurement_array, marker=".", color="red")
            # plt.xlabel("Time - ns")
            # plt.tight_layout()
            # plt.ylabel('Interpolated data')
            # plt.show()

            plt.plot(original_time_array, original_measurement_array, marker="o", color="blue")
            plt.plot(new_time_array, new_measurement_array, marker=".", color="red")
            plt.xlabel("Time - ns")
            plt.ylabel('Original (blue) / Interp. (red)')
            plt.tight_layout()
            plt.show()
    
    #from IPython import embed; embed()
    return status, new_measurement_array, new_time_array
