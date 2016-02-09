from matplotlib.ticker import FuncFormatter
from si_prefix import si_format
from svg_model.plot import plot_shapes_heat_map, plot_color_map_bars
import matplotlib as mpl
import matplotlib.cm as mcm
import pandas as pd
from path_helpers import path


F_formatter = FuncFormatter(lambda x, pos: '%sF' % si_format(x))
m_formatter = FuncFormatter(lambda x, pos: '%sm' % si_format(x, 0))

here_path = path(__file__)
style_path = str(here_path.joinpath('custom.mplstyle'))

# Get median capacitance reading for each channel.
#channel_capacitance = (df_channel_impedances.groupby('channel_i')
                       #['capacitance'].median().ix[channels])


def plot_channel_capacitance(channel_capacitance, vmax=(200e-15),
                             color_map=mcm.Reds_r, **kwargs):
    with mpl.style.context(('ggplot', style_path)):
        axis = plot_color_map_bars(channel_capacitance, color_map=color_map,
                                   vmax=vmax, **kwargs)
        axis.yaxis.set_major_formatter(F_formatter)
        return axis


def plot_electrode_capacitance(df_shapes, channel_capacitance,
                               electrodes_by_channel, vmax=(200e-15),
                               color_map=mcm.Reds_r, **kwargs):
    electrode_ids = electrodes_by_channel.ix[channel_capacitance.index]
    electrode_capacitance = pd.Series(channel_capacitance.ix
                                      [electrode_ids.index].values,
                                      index=electrode_ids.values)

    df_shapes = df_shapes.copy()

    # Scale millimeters to meters.
    df_shapes[['x', 'y']] *= 1e-3

    with mpl.style.context(('ggplot', style_path)):
        axis, colorbar = plot_shapes_heat_map(df_shapes, 'id',
                                              electrode_capacitance,
                                              value_formatter=F_formatter,
                                              vmax=vmax, color_map=color_map,
                                              **kwargs)

        axis.xaxis.set_major_formatter(m_formatter)
        map(lambda t: t.set_rotation(90), axis.get_xticklabels())
        axis.yaxis.set_major_formatter(m_formatter)
        axis.set_aspect(True)
