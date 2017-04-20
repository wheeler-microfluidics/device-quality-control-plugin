"""
Copyright 2015 Christian Fobel

This file is part of device_quality_control_plugin.

device_quality_control_plugin is free software: you can redistribute it and/or
modify it under the terms of the GNU General Public License as published by the
Free Software Foundation, either version 3 of the License, or (at your option)
any later version.

dmf_control_board is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with device_quality_control_plugin.  If not, see <http://www.gnu.org/licenses/>.
"""
import logging
import os

from dmf_control_board_firmware.chip_test.plot import plot_capacitance_summary
from microdrop.gui.channel_sweep import get_channel_sweep_parameters
from microdrop.app_context import get_app, get_hub_uri
from microdrop.plugin_helpers import get_plugin_info
from microdrop.plugin_manager import (PluginGlobals, Plugin, IPlugin,
                                      implements)
from pygtkhelpers.utils import refresh_gui
from path_helpers import path
from zmq_plugin.plugin import Plugin as ZmqPlugin
from zmq_plugin.schema import decode_content_data
import gobject
import gtk
import numpy as np
import pandas as pd
import zmq

logger = logging.getLogger(__name__)

PluginGlobals.push_env('microdrop.managed')


class DeviceQualityControlZmqPlugin(ZmqPlugin):
    '''
    API for adding/clearing droplet routes.
    '''
    def __init__(self, parent, *args, **kwargs):
        self.parent = parent
        super(DeviceQualityControlZmqPlugin, self).__init__(*args, **kwargs)

    def check_sockets(self):
        try:
            msg_frames = self.command_socket.recv_multipart(zmq.NOBLOCK)
        except zmq.Again:
            pass
        else:
            self.on_command_recv(msg_frames)
        return True

    def measure_channel_impedances_monitored(self, **kwargs):
        n_sampling_windows = kwargs.pop('n_sampling_windows', 5)
        channels = kwargs.pop('channels')
        voltage = kwargs.pop('voltage')
        frequency = kwargs.pop('frequency')

        try:
            return self.measure_channel_impedances(channels, voltage,
                                                   frequency,
                                                   n_sampling_windows,
                                                   **kwargs)
        except:
            logger.error(str(kwargs), exc_info=True)

    def on_execute__measure_channel_impedances(self, request):
        data = decode_content_data(request)
        return self.measure_channel_impedances_monitored(**data)

    def on_execute__channel_impedance_structures(self, request):
        data = decode_content_data(request)

        df_channel_impedances = \
            self.measure_channel_impedances_monitored(**data)

        return self.parent.channel_impedance_structures(df_channel_impedances)

    def on_execute__save_channel_impedances(self, request):
        data = decode_content_data(request)

        impedance_structures = data.pop('impedance_structures')
        output_path = data.pop('output_path')
        try:
            self.parent.save_channel_impedances(impedance_structures,
                                                output_path, **data)
        except Exception, error:
            app = get_app()
            if app.config.data.get('advanced_ui', False):
                # Launch debugger to inspect state.
                import pdb; pdb.set_trace()
            raise

    def measure_channel_impedances(self, channels, voltage, frequency,
                                   n_sampling_windows, **kwargs):
        '''
        Parameters
        ----------
        channels : list
            List of channels to scan.
        voltage : float
            Voltage to apply during scan.
        frequency : float
            Frequency to apply during scan.
        n_sampling_windows : int
            Number of sampling windows to collect for each channel.

        Returns
        -------
        pandas.DataFrame
            Table containing one RMS measurement per row and the columns
            ``frequency``, ``voltage``, ``channel_i``, ``V_actuation``,
            ``capacitance``, and ``impedance``.
        '''
        channel_count = self.execute('wheelerlab.dmf_control_board_plugin',
                                     'channel_count', timeout_s=1.,
                                     wait_func=lambda *args: refresh_gui(0, 0))
        assert(all([c < channel_count for c in channels]))

        print '[measure_channel_impedances]', channels

        if 'wait_func' not in kwargs:
           kwargs['wait_func'] = lambda *args: refresh_gui(0, 0)
        channel_states = np.zeros(channel_count, dtype=int)
        channel_states[list(channels)] = 1
        df_result = self.execute('wheelerlab.dmf_control_board_plugin',
                                 'sweep_channels', voltage=voltage,
                                 frequency=frequency, state=channel_states,
                                 n_sampling_windows=n_sampling_windows,
                                 timeout_s=30., **kwargs).dropna()
        return df_result.loc[df_result.V_actuation > .9 * voltage]


class DeviceQualityControlPlugin(Plugin):
    """
    This class is automatically registered with the PluginManager.
    """
    implements(IPlugin)
    version = get_plugin_info(path(__file__).parent).version
    plugin_name = get_plugin_info(path(__file__).parent).plugin_name

    def __init__(self):
        self.name = self.plugin_name
        self.plugin = None
        self.plugin_timeout_id = None

    def on_plugin_enable(self):
        self.cleanup()
        self.plugin = DeviceQualityControlZmqPlugin(self, self.name,
                                                    get_hub_uri())
        # Initialize sockets.
        self.plugin.reset()

        self.plugin_timeout_id = gobject.timeout_add(10,
                                                     self.plugin.check_sockets)

        # Add menu item to launch channel impedance scan.
        menu = [('Run channel impedance scan...', lambda *args:
                 self.channel_impedance_scan()),
                ('Run channel impedance scan (slow)...', lambda *args:
                 self.channel_impedance_scan(slow_scan=True))]

        app = get_app()

        for label_i, func_i in menu:
            menu_item_i = gtk.MenuItem(label_i)
            menu_item_i.connect("activate", func_i)
            app.main_window_controller.menu_tools.add(menu_item_i)
            menu_item_i.show()

    def on_plugin_disable(self):
        """
        Handler called once the plugin instance is disabled.
        """
        self.cleanup()

    def on_app_exit(self):
        """
        Handler called just before the Microdrop application exits.
        """
        self.cleanup()

    def cleanup(self):
        if self.plugin_timeout_id is not None:
            gobject.source_remove(self.plugin_timeout_id)
        if self.plugin is not None:
            self.plugin = None

    def save_channel_impedances(self, impedance_structures, output_path,
                                hdf_root=None, save_plot=False,
                                open_plot=False):
        hdf_root = hdf_root or ''

        # Strip `'/'` characters off `hdf_root` argument since we add `'/'`
        # when joining with relative paths below.
        while hdf_root.endswith('/'):
            hdf_root = hdf_root[:-1]

        for hdf_relpath_i, structure_i in impedance_structures.iteritems():
            hdf_path_i = '/'.join([hdf_root, hdf_relpath_i])
            if hasattr(structure_i, 'columns'):
                data_columns = [c for c in structure_i.columns if '/' not in c]
            else:
                data_columns = True
            structure_i.to_hdf(str(output_path), hdf_path_i, format='t',
                               complib='zlib', complevel=5,
                               data_columns=data_columns)
        if save_plot:
            import matplotlib as mpl

            output_path = path(output_path)
            pdf_path = output_path.parent.joinpath(output_path.namebase +
                                                   '.pdf')
            style_path = (path(__file__).parent
                          .joinpath('custom-style.mplstyle'))

            with mpl.style.context(('ggplot', style_path)):
                axes = plot_capacitance_summary(impedance_structures)
                fig = axes[0].get_figure()
                axes[0].set_title(output_path.namebase)
                fig.savefig(pdf_path, bbox_inches='tight')

            if open_plot:
                # TODO: Add support for opening on Linux/OSX (e.g., `xdg-open`)
                os.startfile(pdf_path)

    def channel_impedance_structures(self, df_channel_impedances):
        hdf_impedance_path = 'channel_impedances'
        hdf_device = 'device'
        hdf_device_shapes = 'shapes'

        device = self.plugin.execute('wheelerlab.device_info_plugin',
                                     'get_device', timeout_s=5.,
                                     wait_func=lambda *args: refresh_gui(0, 0))

        result = {}
        result[hdf_impedance_path] = df_channel_impedances
        result[hdf_device_shapes] = device.df_shapes

        for series_name_i in ('electrodes_by_channel', 'electrode_areas',
                              'channels_by_electrode', 'channel_areas'):
            hdf_path_i = '/'.join([hdf_device, series_name_i])
            data = getattr(device, series_name_i).sort_index()
            result[hdf_path_i] = data
        return result

    def channel_impedance_scan(self, default_filename='channel-impedances.h5',
                               slow_scan=False):
        '''
        Args
        ----

            default_filename (str) : Default filename to display in save
                dialog.
            slow_scan (bool) : If `False`, perform scan using single call to
                Arduino (use firmware scan method).  Otherwise, make one
                firmware call for each scanned channel.  May be useful, for
                example, to verify consistent between slow/fast scans.
        '''
        wait_func = lambda *args: refresh_gui(0, 0)

        dialog = gtk.FileChooserDialog(title='Save channels impedance',
                                       action=gtk.FILE_CHOOSER_ACTION_SAVE,
                                       buttons=(gtk.STOCK_CANCEL,
                                                gtk.RESPONSE_CANCEL,
                                                gtk.STOCK_SAVE,
                                                gtk.RESPONSE_OK))
        file_filter = gtk.FileFilter()
        file_filter.set_name('HDF file (*.h5)')
        file_filter.add_pattern('*.h5')

        app = get_app()
        dialog.set_current_folder(app.experiment_log.get_log_path())
        dialog.set_current_name(default_filename)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_filter(file_filter)

        response = dialog.run()
        output_path = dialog.get_filename()

        dialog.destroy()

        if response != gtk.RESPONSE_OK:
            return

        device = self.plugin.execute('wheelerlab.device_info_plugin',
                                     'get_device', timeout_s=1.,
                                     wait_func=wait_func)

        default_channels = pd.Series(True, index=device.channel_areas.index
                                     .sort_values())
        sweep_parameters = get_channel_sweep_parameters(voltage=100,
                                                        frequency=10e3,
                                                        channels=
                                                        default_channels)
        voltage = sweep_parameters['voltage']
        frequency = sweep_parameters['frequency']
        channels = sweep_parameters['channels']

        # Request scan of channels from 0MQ interface.
        impedance_structures = \
            self.plugin.execute('wheelerlab.device_quality_control_plugin',
                                'channel_impedance_structures',
                                channels=channels, voltage=voltage,
                                frequency=frequency, slow_scan=slow_scan,
                                timeout_s=5 * len(channels),
                                wait_func=wait_func)
        self.plugin.execute('wheelerlab.device_quality_control_plugin',
                            'save_channel_impedances',
                            output_path=output_path,
                            impedance_structures=impedance_structures,
                            save_plot=True, open_plot=True,
                            timeout_s=5 * len(channels),
                            wait_func=wait_func)
        logging.warning('Channel impedances saved.')


PluginGlobals.pop_env()

from ._version import get_versions
__version__ = get_versions()['version']
del get_versions
