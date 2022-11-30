import ctypes as ct
import json
import sys

import matplotlib.pyplot as plt
import numpy as np

from caen_felib import lib, device, error

print(f'CAEN FELib wrapper loaded (lib version {lib.version})')

# Connect
dig = device.Device('dig2://10.105.250.7')

# Get device tree
device_tree = dig.get_device_tree()

# Get board info
nch = int(device_tree['par']['numch']['value'])

# Reset
dig.send_command('/cmd/reset')

# Configure digitizer
reclen = 10240

dig.set_value('/par/TestPulsePeriod', '1000')
dig.set_value('/par/TestPulseWidth', '16')
dig.set_value('/par/AcqTriggerSource', 'TestPulse')
dig.set_value('/par/RecordLengthS', f'{reclen}')
dig.set_value('/par/PreTriggerS', '128')

dig.set_value('/ch/0/par/SamplesOverThreshold', '64')
dig.set_value('/ch/0/par/ITLConnect', 'ITLA')
dig.set_value('/ch/0/par/TriggerThr', '200')
dig.set_value('/ch/0/par/TriggerThrMode', 'Absolute')
dig.set_value('/ch/0/par/SelfTriggerEdge', 'Fall')

for i in range(nch):
	dig.set_value(f'/ch/{i}/par/DCOffset', f'{20 + i}')
	dig.set_value(f'/ch/{i}/par/WaveDataSource', 'Ramp')

dig.set_value('/endpoint/par/activeendpoint', 'scope')
ep_scope = dig.endpoints['scope']

data_format = [
	{
		'name': 'EVENT_SIZE',
		'type': 'SIZE_T',
	},
	{
		'name': 'TIMESTAMP',
		'type': 'U64',
	},
	{
		'name': 'WAVEFORM',
		'type': 'U16',
		'dim': 2,
		'shape': [nch, reclen],
	},
	{
		'name': 'WAVEFORM_SIZE',
		'type': 'U64',
		'dim': 1,
		'shape': [nch],
	},
]

ep_scope.set_read_data_format(data_format)

# Configure plot
plt.ion()
figure, ax = plt.subplots(figsize=(10, 8))
lines = []
for i in range(4):
	line, = ax.plot([], [])
	lines.append(line)
ax.set_ylim(0, 2 ** 14 - 1)

# Initialize data
event_size = ep_scope.data[0].value
timestamp = ep_scope.data[1].value
waveform = ep_scope.data[2].value
waveform_size = ep_scope.data[3].value

# Start acquisition
dig.send_command('/cmd/armacquisition')
dig.send_command('/cmd/swstartacquisition')

while True:

	try:
		ep_scope.read_data(-1)
	except error.Timeout as ex:
		print('timeout')
		continue
	except error.Stop as ex:
		print('stop')
		break

	for i in range(4):
		lines[i].set_data(range(waveform_size[i]), waveform[i])

	ax.relim()
	ax.autoscale_view(True, True, False)
	figure.canvas.draw()
	figure.canvas.flush_events()

dig.send_command('/cmd/disarmacquisition')
