"""
Copyright 2015 Christian Fobel

This file is part of device_quality_control_plugin.

device_quality_control_plugin is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

dmf_control_board is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with device_quality_control_plugin.  If not, see <http://www.gnu.org/licenses/>.
"""
#from collections import OrderedDict
from datetime import datetime
import logging

#from flatland import Integer, Form
#from flatland.validation import ValueAtLeast
from microdrop.app_context import get_hub_uri
from microdrop.plugin_helpers import get_plugin_info
from microdrop.plugin_manager import (PluginGlobals, Plugin, IPlugin,
                                      implements)
from pygtkhelpers.utils import refresh_gui
from path_helpers import path
from zmq_plugin.plugin import Plugin as ZmqPlugin
from zmq_plugin.schema import decode_content_data
import gobject
import gtk
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
        def wait_func(duration_s, i, channel_i):
            for j in xrange(20):
                gtk.do_main_iteration()
            print '\r[%s] %5d/%d' % (channel_i, i, len(kwargs['channels']))

        try:
            return self.measure_channel_impedances(kwargs['channels'],
                                                   kwargs['voltage'],
                                                   kwargs['frequency'],
                                                   wait_func=wait_func)
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

        output_path = data['output_path']
        hdf_root = data.get('hdf_root')
        self.parent.save_channel_impedances(data['impedance_strutures'],
                                            output_path, hdf_root)

    def measure_channel_impedances(self, channels, voltage, frequency,
                                   wait_func=None, **kwargs):
        channel_count = self.execute('wheelerlab.dmf_control_board_plugin',
                                     'channel_count', timeout_s=1.)
        assert(all([c < channel_count for c in channels]))

        frames = []

        for i, channel_i in enumerate(channels):
            channel_states = [0] * channel_count
            channel_states[channel_i] = 1
            start_time = datetime.utcnow()
            if wait_func is not None:
                def wait_func_i(duration_s):
                    wait_func(duration_s, i, channel_i)
                kwargs['wait_func'] = wait_func_i
            try:
                df_result_i = \
                    self.execute('wheelerlab.dmf_control_board_plugin',
                                 'measure_impedance', voltage=voltage,
                                 frequency=frequency, state=channel_states,
                                 timeout_s=5., **kwargs).dropna()
            except RuntimeError:
                break
            df_result_i.insert(2, 'channel_i', channel_i)
            df_result_i.insert(0, 'utc_start', start_time)
            frames.append(df_result_i)
        if not frames:
            df_result = pd.DataFrame(None, columns=['utc_start', 'seconds',
                                                    'channel_i', 'frequency',
                                                    'V_actuation',
                                                    'capacitance',
                                                    'impedance'])
        else:
            df_result = pd.concat(frames)
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
                                hdf_root=None):
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

    def channel_impedance_structures(self, df_channel_impedances):
        hdf_impedance_path = 'channel_impedances'
        hdf_device = 'device'
        hdf_device_shapes = 'shapes'

        device = self.plugin.execute('wheelerlab.device_info_plugin',
                                     'get_device', timeout_s=5.)

        result = {}
        result[hdf_impedance_path] = df_channel_impedances
        result[hdf_device_shapes] = device.df_shapes

        for series_name_i in ('electrodes_by_channel', 'electrode_areas',
                              'channels_by_electrode', 'channel_areas'):
            hdf_path_i = '/'.join([hdf_device, series_name_i])
            data = getattr(device, series_name_i).sort_index()
            result[hdf_path_i] = data
        return result


PluginGlobals.pop_env()
