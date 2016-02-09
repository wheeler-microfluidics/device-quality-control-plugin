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

    def on_execute__measure_channel_impedances(self, request):
        data = decode_content_data(request)

        def wait_func(duration_s, i, channel_i):
            for j in xrange(20):
                gtk.do_main_iteration()
            print '\r[%s] %5d/%d' % (channel_i, i, len(data['channels']))

        try:
            return self.measure_channel_impedances(data['channels'],
                                                   data['voltage'],
                                                   data['frequency'],
                                                   wait_func=wait_func)
        except:
            logger.error(str(data), exc_info=True)

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

    '''
    StepFields
    ---------

    A flatland Form specifying the per step options for the current plugin.
    Note that nested Form objects are not supported.

    Since we subclassed StepOptionsController, an API is available to access and
    modify these attributes.  This API also provides some nice features
    automatically:
        -all fields listed here will be included in the protocol grid view
            (unless properties=dict(show_in_gui=False) is used)
        -the values of these fields will be stored persistently for each step
    '''
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

    def save_channel_impedances(self, df_channel_impedances, output_path,
                                hdf_root='/'):
        device = self.plugin.execute('wheelerlab.device_info_plugin',
                                     'get_device', timeout_s=1.)

        hdf_impedance_path = '/'.join([hdf_root, 'channel_impedances'])
        hdf_device = '/'.join([hdf_root, 'device'])
        hdf_device_shapes = '/'.join([hdf_device, 'shapes'])

        df_channel_impedances.to_hdf(str(output_path), hdf_impedance_path,
                                     format='t', complib='zlib', complevel=5,
                                     data_columns=True)
        device.df_shapes.to_hdf(str(output_path), hdf_device_shapes,
                                format='t', complib='zlib', complevel=5,
                                data_columns=[c for c in device.df_shapes
                                              .columns if '/' not in c])

        for series_name_i in ('electrodes_by_channel', 'electrode_areas',
                              'channels_by_electrode', 'channel_areas'):
            hdf_path_i = '/'.join([hdf_device, series_name_i])
            data = getattr(device, series_name_i).sort_index()
            data.to_hdf(str(output_path), hdf_path_i, format='t',
                        complib='zlib', complevel=5, data_columns=True)


PluginGlobals.pop_env()
