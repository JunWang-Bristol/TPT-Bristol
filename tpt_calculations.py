import pandas as pd
import os
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import SpanSelector
import math

import tpt_settings
import tpt_automation
import draggable_lines_class

def TPT_coreloss_calculation(df, df_params):
    # pixels_h = 1300 (good results with these values)
    # pixels_v = 975
    # scale_hw: by default, False. If True -> use tpt_settings.max_input_after_attenuation...
    # Extract df columns
    time_array = df["Time"]
    Vp_array = df["Vp"]
    Vs_array = df["Vs"]
    Cp_array = df["Cp"]

    T1_ns = df_params["T1_ns"].values
    T2A_ns = df_params["T2A_ns"].values
    T2B_ns = df_params["T2B_ns"].values



    Actual_sample_time = df_params["actual_sample_time"]
    Actual_pre_sample = 100 * Actual_sample_time/5
    sampling_time = math.floor(time_array[10]-time_array[9])

    T_start = math.floor((T1_ns + T2A_ns +2*(T2A_ns +T2B_ns))/sampling_time + Actual_pre_sample)
    T_stop = math.floor((T1_ns + T2A_ns + T2A_ns + T2B_ns + 2*(T2A_ns +T2B_ns))/sampling_time+ Actual_pre_sample)
    current_array = Cp_array[T_start:T_stop]
    voltage_array = Vs_array[T_start:T_stop]
    voltage_array = voltage_array.values
    current_array = current_array.values
    i = 0
    Q_loss = [0] * 20000
    for i in range(0, len(voltage_array)):
        Q_loss[i] = voltage_array[i] * current_array[i]
        i = i + 1
    Q_sum = np.sum(Q_loss)
    P = Q_sum * sampling_time * 1e-09
    
    return P



def plot_TPT_df_electrical(df, str_title, pixels_h, pixels_v, scale_hw=False):
    # pixels_h = 1300 (good results with these values)
    # pixels_v = 975
    # scale_hw: by default, False. If True -> use tpt_settings.max_input_after_attenuation...
    # Extract df columns
    time_array = df["Time"]
    Vp_array = df["Vp"]
    Vs_array = df["Vs"]
    Cp_array = df["Cp"]

    # Time string formatting
    time_array_plot = time_array
    str_time = "Time (ns)"
    max_time_ns = max(time_array)
    if(max_time_ns > 25000 and max_time_ns <= 25000000):
        time_array_plot = time_array / 1000
        str_time = "Time (us)"
    elif(max_time_ns > 25000000 and max_time_ns < 25000000000):
        time_array_plot = time_array / 1000000
        str_time = "Time (ms)"

    # Create figure
    figure, (ax_Vp, ax_Cp, ax_Vs) = plt.subplots(3, 1)

    # plot data from channels A, B and C
    ax_Vp.plot(time_array_plot, Vp_array)  # volts
    ax_Vp.set_xlabel(str_time)
    ax_Vp.set_ylabel('Primary\nvoltage (V)')
    if scale_hw is True:
        ax_Vp.set_ylim(-tpt_settings.max_input_after_attenuation_A_V, tpt_settings.max_input_after_attenuation_A_V)
    
    ax_Cp.plot(time_array_plot, Cp_array)  # amps
    ax_Cp.set_xlabel(str_time)
    ax_Cp.set_ylabel('Primary\ncurrent (A)')
    if scale_hw is True:
        ax_Cp.set_ylim([-tpt_settings.max_input_after_attenuation_C_A, tpt_settings.max_input_after_attenuation_C_A])
    
    ax_Vs.plot(time_array_plot, Vs_array)  # volts
    ax_Vs.set_xlabel(str_time)
    ax_Vs.set_ylabel('Secondary\nvoltage (V)')
    if scale_hw is True:
        ax_Vs.set_ylim([-tpt_settings.max_input_after_attenuation_B_V, tpt_settings.max_input_after_attenuation_B_V])
    #figure = plt.gcf()  # get current figure
    #pixels_h = 1300
    #pixels_v = 975
    figure.set_size_inches(pixels_h / tpt_settings.DPI, pixels_v / tpt_settings.DPI)
    figure.suptitle(str_title)
    figure.tight_layout()
    plt.ion()
    plt.show(block=False)
    #plt.pause(3)
    plt.pause(0.1)
    print("Plotting graphs. Please close them before proceeding with the test.")
    
    return figure, [ax_Vp, ax_Cp, ax_Vs]


def plot_TPT_df_magnetic(df, title_time, title_loop, pixels_fig_time, pixels_fig_loop):
    # pixels_h = 1300 (good results with these values)
    # pixels_v = 975
    # extract figure dimensions
    pixels_h_time = pixels_fig_time[0]
    pixels_v_time = pixels_fig_time[1]
    pixels_h_loop = pixels_fig_loop[0]
    pixels_v_loop = pixels_fig_loop[1]
    # Extract df columns
    time_array = df["Time"]
    B_array = df["B"]
    H_array = df["H"]

    # Time string formatting
    time_array_plot = time_array
    str_time = "Time (ns)"
    max_time_ns = max(time_array)
    if(max_time_ns > 25000 and max_time_ns <= 25000000):
        time_array_plot = time_array / 1000
        str_time = "Time (us)"
    elif(max_time_ns > 25000000 and max_time_ns < 25000000000):
        time_array_plot = time_array / 1000000
        str_time = "Time (ms)"

    # Create figure
    figure_time, (ax_B, ax_H) = plt.subplots(2, 1)

    ax_B.plot(time_array_plot, B_array)  # volts
    ax_B.set_xlabel(str_time)
    ax_B.set_ylabel('B - Magnetic flux\ndensity (T)')
    
    ax_H.plot(time_array_plot, H_array)  # amps
    ax_H.set_xlabel(str_time)
    ax_H.set_ylabel('H - Magnetic field\nstrength (A/m)')
    
    figure_time.set_size_inches(pixels_h_time / tpt_settings.DPI, pixels_v_time / tpt_settings.DPI)
    figure_time.suptitle(title_time)
    figure_time.tight_layout()
    plt.ion()
    plt.show(block=False)
    plt.pause(0.1)
    
    # BH plot
    figure_loop = plt.figure()
    ax_loop = plt.axes()
    ax_loop.plot(H_array, B_array)
    ax_loop.set_xlabel('H - Magnetic field\nstrength (A/m)')
    ax_loop.set_ylabel('B - Magnetic flux\ndensity (T)')
    figure_loop.set_size_inches(pixels_h_loop / tpt_settings.DPI, pixels_v_loop / tpt_settings.DPI)
    figure_loop.suptitle(title_loop)
    figure_loop.tight_layout()
    
    plt.ion()
    plt.show(block=False)
    plt.pause(0.1)

    return figure_time, [ax_B, ax_H], figure_loop, ax_loop


def plot_single_variable(df, var, title_str, pixels_h, pixels_v):
    # functions used to plot a single variable 
    #var: "Vp", "Vs", etc.
    time_array = df["Time"].to_numpy()
    # TODO check that var exists in df
    var_array = df[var].to_numpy()
    
    str_time = "Time (ns)"
    str_var = "N/A"
    if(var == "Vp"):
        str_var = "Primary voltage (V)"
    elif(var == "Vs"):
        str_var = "Secondary voltage (V)"
    elif(var == "Cp"):
        str_var = "Primary current (A)"
    elif(var == "B"):
        str_var = "B (T)"
    elif(var == "B"):
        str_var = "H (A/m)"
    figure = plt.figure()
    ax = plt.axes()
    ax.plot(time_array, var_array)  # volts
    ax.set_xlabel(str_time)
    ax.set_ylabel(str_var)
    figure.set_size_inches(pixels_h / tpt_settings.DPI, pixels_v / tpt_settings.DPI)
    figure.suptitle(title_str)
    figure.tight_layout()
    plt.ion()
    plt.show(block=False)
    plt.pause(0.1)
    return figure, ax


def TPT_guided_processing():
    import os.path
    clear_terminal()
    print("+---------------------------------------------+")
    print("|    Guided processing of TPT test results    |")
    print("+---------------------------------------------+")
    print("")
    path_folder_Outputs = "Outputs/"
    list_folders = next(os.walk(path_folder_Outputs))[1]
    list_folders_TPT = []
    folder_TPT_index = 0
    for x in range(0, len(list_folders)):
        folder_name = list_folders[x]
        if(folder_name.find("Test TPT") != -1):
            list_folders_TPT.append(folder_name)
            print(folder_TPT_index, "- Folder: ", folder_name)
            folder_TPT_index = folder_TPT_index + 1
    # print(list_folders_TPT)
    index_folder_TPT = int(input("\nPlease indicate the index of a folder to process its contents: "))
    folder_TPT = list_folders_TPT[index_folder_TPT]
    print(path_folder_Outputs + folder_TPT)
    iteration_index_folder_TPT = int(input("\nPlease indicate the iteration number (00,01,02,...): "))
    iteration_index_folder_TPT = str(iteration_index_folder_TPT).zfill(2)
    # next - check that the dataframe file exists
    path_df = path_folder_Outputs + folder_TPT + "/" + folder_TPT[0:-8] + "Iteration" + " " + iteration_index_folder_TPT + " - " + "TPT Measurements.csv"
    print(path_df)
    path_df_params = path_folder_Outputs + folder_TPT + "/" + folder_TPT[0:-8] + "Iteration" + " " + iteration_index_folder_TPT + " - " +"TPT Parameters.csv"
    df_file_exists = os.path.exists(path_df)
    if(df_file_exists is True):
        df = pd.read_csv(path_df)
        df_params = pd.read_csv(path_df_params)
        tpt_automation.load_TPT_test_parameters(df_params)  # load geometrical + electrical parameters

        # TODO w = 0.4 ... check if it still holds true [26/08/2022]
        df = add_magnetic_variables_to_df(df, 0.4)
        fig1_handle, _ = plot_TPT_df_electrical(df, "Electrical variables vs. time - TPT acquisition", 1300, 975)
        fig2_handle, _, fig3_handle, _ = plot_TPT_df_magnetic(df, "Magnetic variables vs. time\nTPT acquisition", "BH loop - TPT acquisition", [540, 405], [500, 500])
        fig_select_handle, ax_select_handle = plot_single_variable(df, "Vp", "Select the first edge of the Stage III", 1200, 400)
        Coreloss = TPT_coreloss_calculation(df,df_params)
        print("The coreloss is " + str(Coreloss) + "J")

        # def onselect(xmin, xmax):
        #     x = df["Time"].to_numpy()
        #     indmin, indmax = np.searchsorted(x, (xmin, xmax))
        #     #from IPython import embed; embed()
        #     fig_select_handle.canvas.draw_idle()
        #     tpt_settings.index_min_edge_TPT_plot = indmin
        #     tpt_settings.index_max_edge_TPT_plot = indmax
        #     plt.close(fig_select_handle)
        #     #close figure, trim df, plot df (with BH loops included)
        # span = SpanSelector(ax_select_handle, onselect, 'horizontal', useblit=True,
        #                     props=dict(alpha=0.5, facecolor='red'))
        # input("\nPress Enter to continue once the region is selected.")
        # # Calculate zero-volt crossing (Vp) of the selected edge. That marks the start of Stage III
        # Vp_trim = df["Vp"][tpt_settings.index_min_edge_TPT_plot:tpt_settings.index_max_edge_TPT_plot]
        # index_start_stage_III = tpt_settings.index_min_edge_TPT_plot + find_index_nearest(Vp_trim, 0)
        # #from IPython import embed; embed()
        #
        # df_34 = df
        # # df_34 = df_34.drop(df.index[tpt_settings.index_max_edge_TPT_plot:-1])
        # df_34 = df_34.drop(df_34.index[0:index_start_stage_III])
        # df_34 = df_34.reset_index(drop=True)
        # # Trim also to closed loop
        # # We need a closed BH loop, so
        # B_34 = df_34["B"].to_numpy()
        # time_34 = df_34["Time"].to_numpy()
        # B_34_start = B_34[0]
        # time_34_start = time_34[0]
        # # now we define a search region which should correspond roughly to that where the BH loop is closed
        # # that is, when B reaches the same value that had in the beginning of the cycle
        # T2_ns = tpt_settings.T2A_ns + tpt_settings.T2B_ns
        # time_3_search_start = time_34_start + round(0.9 * T2_ns)
        # time_3_search_end = time_34_start + round(1.1 * T2_ns)
        # index_3_search_start = find_index_nearest(time_34, time_3_search_start)
        # index_3_search_end = find_index_nearest(time_34, time_3_search_end)
        #
        # B_34_trimmed = B_34[index_3_search_start:index_3_search_end]
        # index_end_3 = index_3_search_start + find_index_nearest(B_34_trimmed, B_34_start)
        # df_3 = df_34
        # df_3_last_index = df_3.index[-1]
        # df_3 = df_3.drop(df_3.index[index_end_3 + 1:df_3_last_index+1])
        # df_3 = df_3.reset_index(drop=True)
        #
        # fig4_handle, _ = plot_TPT_df_electrical(df_3, "Electrical variables vs. time - Stage III", 1300, 975)
        # fig5_handle, _, fig6_handle, _ = plot_TPT_df_magnetic(df_3, "Magnetic variables vs. time - Stage III", "BH loop - Stage III", [540, 405], [500, 500])
        # e_p, e_s, p_p, p_s, delta_e_p, delta_e_s, delta_p_p, delta_p_s = calculate_VI_losses(df, 1)
        # # arrange plots
        # # TODO ARRANGE THE PLOTS AND THEN OBTAIN THE DESIRED DIMENSIONS
        # mgr_1 = fig1_handle.canvas.manager
        # mgr_2 = fig2_handle.canvas.manager
        # mgr_3 = fig3_handle.canvas.manager
        # mgr_4 = fig4_handle.canvas.manager
        # mgr_5 = fig5_handle.canvas.manager
        # mgr_6 = fig6_handle.canvas.manager
        # geom_1 = mgr_1.window.geometry()
        # x1, y1, dx1, dy1 = geom_1.getRect()
        # geom_2 = mgr_2.window.geometry()
        # x2, y2, dx2, dy2 = geom_2.getRect()
        # geom_3 = mgr_3.window.geometry()
        # x3, y3, dx3, dy3 = geom_3.getRect()
        # geom_4 = mgr_4.window.geometry()
        # x4, y4, dx4, dy4 = geom_4.getRect()
        # geom_5 = mgr_5.window.geometry()
        # x5, y5, dx5, dy5 = geom_5.getRect()
        # geom_6 = mgr_6.window.geometry()
        # x6, y6, dx6, dy6 = geom_6.getRect()
        #
        # mgr_1.window.setGeometry(5, 35, 800, 375)
        # mgr_2.window.setGeometry(800, 35, 375, 375)
        # mgr_3.window.setGeometry(1175, 35, 375, 375)
        # mgr_4.window.setGeometry(5, 440, 800, 375)
        # mgr_5.window.setGeometry(800, 440, 375, 375)
        # mgr_6.window.setGeometry(1175, 440, 375, 375)
        # from IPython import embed; embed()
    else:
        print("The dataframe file could not be found in the selected folder.")
    input("\nPress Enter to continue.")
    plt.close('all')
    clear_terminal()


def clear_terminal():
    os.system('cls' if os.name == 'nt' else 'clear')


def add_magnetic_variables_to_df(df, w):
    # this code was developed for APEC 22
    Np = tpt_settings.N1
    Ns = tpt_settings.N2
    Ae = tpt_settings.Ae
    le = tpt_settings.le
    # Extract acquisition variables
    time = df["Time"].to_numpy()
    sampling_time = (time[1]-time[0]) * 1e-9  #ns
    length = df.shape[0]
    
    # Calculate B
    Vs = df["Vs"].to_numpy()
    B = np.zeros(length)
    for x in range(1, length):
        B[x] = B[x-1] + (Vs[x] + Vs[x-1])*sampling_time/2  # integration
    B = B / Ae / Ns

    # Calculate H
    Cp = df["Cp"].to_numpy()
    
    # Correct current offset
    Cp_corr = Cp - sum(Cp[0:49])/50
    df['Cp'] = Cp_corr
    #from IPython import embed; embed()

    H = Cp_corr * (Np / le)
    H = filter(H, w)
    # Add B, H columns to dataframe
    df['B'] = B.tolist()
    df['H'] = H.tolist()
    return df


def find_index_nearest(array, value):
    array = np.asarray(array)
    idx = (np.abs(array - value)).argmin()
    return idx


def filter(a, w):
    f = np.zeros(a.size)
    f[0] = a[0]
    for x in range(1, a.size):
        f[x] = (1-w)*f[x-1] + w*a[x]
    return f


def calculate_VI_losses(df, print_data):
    # Take as an input the third stage of the TPT pulse test
    Vp = df["Vp"].to_numpy()
    Vs = df["Vs"].to_numpy()
    Cp = df["Cp"].to_numpy()
    t = df["Time"].to_numpy()
    dt = (t[1]-t[0]) * 1e-9  # time differential
    n_samples = Vp.size
    # Filter data
    Vp = filter(Vp, 0.99)
    Vs = filter(Vs, 0.99)
    Cp = filter(Cp, 0.99)
    # Calculate losses
    p_p = np.zeros(n_samples)   # Power Primary
    p_s = np.zeros(n_samples)   # Power Secondary
    e_p = np.zeros(n_samples)   # Energy Primary
    e_s = np.zeros(n_samples)   # Energy Secondary
    for i in range(0, n_samples):
        p_p[i] = Vp[i] * Cp[i]
        p_s[i] = (tpt_settings.N1/tpt_settings.N2) * Vs[i] * Cp[i]
        if i > 0:
            e_p[i] = e_p[i-1] + p_p[i] * dt
            e_s[i] = e_s[i-1] + p_s[i] * dt
    period_t = (t[-1] - t[0]) * 1e-9  # period time
    # Calculate variation of energy and corresponding power during cycle
    delta_e_p = e_p[-1]-e_p[0]
    delta_e_s = e_s[-1]-e_s[0]
    delta_p_p = delta_e_p / period_t
    delta_p_s = delta_e_s / period_t
    if print_data == 1:
        print("Total energy variation during cycle:", delta_e_p, "J")
        print("Core energy variation during cycle:", delta_e_s, "J")
        print("Total power during cycle:", round(delta_p_p, 4), "W")
        print("Total core power during cycle:", round(delta_p_s, 4), "W")
        print("Total volumetric core power (Pv) during cycle:", round(delta_p_s / (1000 * tpt_settings.Ve), 4), "kW/m3")
        print("Total winding power during cycle:", round(delta_p_p-delta_p_s, 4), "W")
    return e_p, e_s, p_p, p_s, delta_e_p, delta_e_s, delta_p_p, delta_p_s


# def get_edges(V):
#     # returns two numpy arrays (rising, falling) with the indices corresponding to the 
#     # rising/falling edges of the vector 
#     length = V.size
#     # Filter input array (first heavily to remove peaks, then lightly to process)
#     V_filtered = filter(V, 0.05)
#     max_V = np.amax(V_filtered)
#     min_V = np.amin(V_filtered)
#     V_filtered = filter(V, 0.9)
#     # Build an array such that values:
#     #   close to V_max =>  1
#     #   close to 0     =>  0
#     #   close to V_min => -1
#     V_discrete = np.zeros(length)
#     for i in range(0, length):
#         if V_filtered[i] > max_V/2:
#             V_discrete[i] = 1
#         elif V_filtered[i] < min_V/2:
#             V_discrete[i] = -1
#     # Now identify rising and falling edges
#     rising_array = np.zeros(length)
#     falling_array = np.zeros(length)
#     for i in range(1, length):
#         derivative = V_discrete[i] - V_discrete[i-1]
#         if derivative > 0:
#             rising_array[i] = 1
#         elif derivative < 0:
#             falling_array[i] = 1
#     # Second derivative in case there is a -1, 0, 1 sequence
#     for i in range(1, length):
#         derivative = rising_array[i] - rising_array[i-1]
#         if derivative > 0:
#             rising_array[i] = 1
#         else:
#             rising_array[i] = 0
#     for i in range(1, length):
#         derivative = falling_array[i] - falling_array[i-1]
#         if derivative > 0:
#             falling_array[i] = 1
#         else:
#             falling_array[i] = 0
#     rising_indices = np.asarray(np.where(rising_array == 1))
#     falling_indices = np.asarray(np.where(falling_array == 1))
#     return rising_array, falling_array, rising_indices, falling_indices
