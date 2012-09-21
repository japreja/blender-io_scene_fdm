'''
Write aircraft data to file(s)

@author: tom
'''
import math, mathutils
from mathutils import Vector
from os import path

ft2m = 0.3048

import bpy, time, datetime
from bpy_extras.io_utils import ExportHelper
from . import aircraft, util

class AnimationsFGFS:
	'''Exporter for flightgear animations'''
	
	def __init__(self):
		self.model = util.XMLDocument('PropertyList')
		self.obs_transparent = []

	def save(self, filename):
		
		# Let transparent objects use the model-transparent effect to be compatible
		# with Rembrandt rendering of FlightGear
		if len(self.obs_transparent):
			eff = self.model.createChild('effect')
			eff.createPropChild('inherits-from', 'Effects/model-transparent')
			for ob in self.obs_transparent:
				eff.createPropChild('object-name', ob.name)

		self.model.createChild('path', path.basename(filename) + '.ac')

		f = open(filename + '.model.xml', 'w')
		self.model.writexml(f, "", "\t", "\n")
		f.close()

	def addGear(self, gear, i):
		'''
		@param ob_strut	Gear data
		@param i				Gear index
		'''
		node = 'gear/gear['+str(i)+']/'
		
		# Compression
		self.addAnimation(
			'translate',
			gear['ob'],
			node + 'compression-norm',
			axis = [0,0,1],
			factor = ft2m, # TODO check yasim
			offset = -gear['current-compression']
		)
		
		# Steering
		if gear['gear'].steering_type == 'STEERABLE':
			self.addAnimation(
				'rotate',
				gear['ob'],
				node + 'steering-norm',
				axis = [0,0,-1],
				factor = math.degrees(gear['gear'].max_steer)
			)
		else:
			# TODO check CASTERED
			pass
		
		# Wheel spin
		dist = gear['wheels'][0]['diameter'] * math.pi
		self.addAnimation(
			'spin',
			[w['ob'] for w in gear['wheels']],
			node + 'rollspeed-ms',
			axis = [0,-1,0],
			factor = 60 / dist, # dist per revolution to rpm
			offset = -gear['current-compression']
		)
		
		# Tyre smoke
		m = self.model.createChild('model')
		m.createPropChild('path', "Aircraft/Generic/Effects/tyre-smoke.xml")
		p = m.createChild('overlay').createChild('params')
		p.createPropChild('property', node + 'tyre-smoke')
		m.createVectorChild('offsets', gear['location'], '-m')
	
	def addAnimation(	self,	anim_type, obs,
											prop = None,
											axis = None,
											factor = None,
											offset = None,
											table = None ):
		'''
		@param anim_type	Animation type
		@param obs				Single or list of objects names to be animated
		@param prop				Property used to control animation
		'''
		a = self.model.createChild('animation')
		a.createPropChild('type', anim_type)

		# ensure it's a list
		if not isinstance(obs, list):
			obs = [obs]
		for ob in obs:
			a.createPropChild('object-name', ob.name)

		if prop != None:
			a.createPropChild('property', prop)

		if factor != None:
			a.createPropChild('factor', factor)

		if offset != None:
			tag = 'offset'
			if anim_type == 'translate':
				tag += '-m'
			elif anim_type == 'rotate':
				tag += '-deg'
			a.createPropChild(tag, offset)
			
		if table != None:
			tab = a.createChild('interpolation')
			for entry in table:
				e = tab.createChild('entry')
				e.createPropChild('ind', entry[0])
				e.createPropChild('dep', entry[1])
		
		if anim_type in ['rotate', 'spin']:
			a.createCenterChild(obs[0])
		
		if axis != None:
			a.createVectorChild('axis', axis)
		
		return a
	
	def addTransparentObject(self, ob):
		self.obs_transparent.append(ob)

class Exporter(bpy.types.Operator, ExportHelper):
	'''Export to Flightgear FDM (.xml)'''
	bl_idname = 'export_scene.fdm'
	bl_label = 'Export Flightgear FDM'
	bl_options = {'PRESET'}
	
	filename_ext = '.xml'
	
	def execute(self, context):
		t = time.mktime(datetime.datetime.now().timetuple())
		
		self.gear_index = 0
		self.exp_anim = AnimationsFGFS()
		self.ground_reactions = util.XMLDocument('ground_reactions')
		
		for ob in bpy.data.objects:
			if not ob.is_visible(context.scene):
				continue
			
			self.checkTransparency(ob)
			
			if ob.fgfs.type == 'STRUT':
				self.exportGear(ob)
			elif ob.fgfs.type == 'PICKABLE':
				self.exportPickable(ob)
			elif ob.type == 'LAMP':
				self.exportLight(ob)

			self.exportDrivers(ob)
		
		f = open(self.filepath, 'w')
		self.ground_reactions.writexml(f, "", "\t", "\n")
		f.close()
		
		file_name = path.splitext(self.filepath)
		self.exp_anim.save(file_name[0])
	
		t = time.mktime(datetime.datetime.now().timetuple()) - t
		print('Finished exporting in', t, 'seconds')
	
		return {'FINISHED'}
	
	def exportGear(self, ob):
		gear = aircraft.gear.parse(ob)
		c = self.ground_reactions.createChild('contact')
		c.setAttribute('type', 'BOGEY')
		c.setAttribute('name', ob.name)
		
		l = c.createVectorChild('location', gear['location'])
		l.setAttribute('unit', 'M')
		
		c.createPropChild('static_friction', 0.8)
		c.createPropChild('dynamic_friction', 0.5)
		c.createPropChild('rolling_friction', 0.02)
		
		strut = gear['strut']
		c.createPropChild('spring_coeff', strut.spring_coeff, 'N/M')
		c.createPropChild('damping_coeff', strut.damping_coeff, 'N/M/SEC')
		
		if gear['gear'].steering_type == 'FIXED':
			max_steer = 0
		elif gear['gear'].steering_type == 'CASTERED':
			max_steer = 360
		else:
			max_steer = gear['gear'].max_steer
		c.createPropChild('max_steer', max_steer, 'DEG')
		
		c.createPropChild('brake_group', gear['gear'].brake_group)
		c.createPropChild('retractable', 1)
		
		self.exp_anim.addGear(gear, self.gear_index)
		self.gear_index += 1
	
	def exportDrivers(self, ob):
		
		if not ob.animation_data:
			return

		# object center in world coordinates
		center = ob.matrix_world.to_translation()
		
		for driver in ob.animation_data.drivers:
			# get the animation axis in world coordinate frame
			# (vector from center to p2)
			p2 = Vector([0,0,0])
			p2[ driver.array_index ] = 1
			
			axis = (ob.matrix_world * p2) - center
			prop = None
			factor = 1
			offset = 0
			table = None
			
			for var in driver.driver.variables:
				if var.type == 'SINGLE_PROP':
					if len(var.targets) != 1:
						raise RuntimeError('SINGLE_PROP: wrong target count', var.targets)
				else:
					raise RuntimeError('Exporting ' + var.type + ' not supported yet!')
			
				tar = var.targets[0]
				if tar.id_type in ['OBJECT', 'SCENE', 'WORLD']:
					prop = var.targets[0].data_path.strip('["]')
			
			if not prop:
				raise RuntimeError('No property!')
			
			if len(driver.keyframe_points):
				table = [k.co for k in driver.keyframe_points]
			else:
				for mod in driver.modifiers:
					if mod.type != 'GENERATOR':
						print('Driver: modifier type=' + mod.type + ' not supported yet!')
						continue
					
					if mod.poly_order != 1:
						print('Driver: polyorder != 1 not supported yet!')
						continue
					
					factor = mod.coefficients[1]
					
					# we don't need to get the offset coefficient as blender already has
					# applied it to the model for us. We need just to remove the offset
					# introduced by the current value of the property (if not zero)
					offset = -tar.id[prop] * factor
				
			if driver.data_path == 'rotation_euler':
				if table:
					of = ob.rotation_euler[ driver.array_index ]
					table = [[k[0], round(math.degrees(k[1] - of), 1)] for k in table]
				else:
					factor = math.degrees(factor)
					offset = math.degrees(offset)
				anim_type = 'rotate'
			elif driver.data_path == 'location':
				anim_type = 'translate'
			else:
				print('Exporting ' + driver.data_path + ' not supported yet!')
				continue
			
			# TODO check for real gear index
			prop = prop.replace('/gear/', 'gear/gear[0]/')
			
			if table:
				self.exp_anim.addAnimation(
					anim_type,
					ob,
					axis = axis,
					prop = prop,
					table = table
				)
			else:
				self.exp_anim.addAnimation(
					anim_type,
					ob,
					prop = prop,
					axis = axis,
					factor = factor,
					offset = offset
				)
				
	def exportPickable(self, ob):
		props = ob.fgfs.clickable

		action = self.exp_anim.addAnimation('pick', ob).createChild('action')
		action.createChild('button', 0)
		binding = action.createChild('binding')
		binding.createChild('command', props.action)
		prop = props.prop
		if len(prop) == 0:
			prop = '/controls/instruments/'+ob.parent.name+'/input'
		binding.createChild('property', prop)
		if props.action in ['property-assign']:
			binding.createChild('value', ob.name)
	
	def exportLight(self, ob):
		if ob.data.type != 'SPOT':
			return
		
		m = self.exp_anim.model.createChild('model')
		m.createPropChild('path', "Aircraft/Generic/Lights/light-cone.xml")
		m.createPropChild('name', ob.name)
		o = m.createVectorChild('offsets', ob.matrix_world.to_translation(),'-m')
		o.createPropChild('pitch-deg', -5)
		p = m.createChild('overlay').createChild('params')
		p.createPropChild('switch', "/controls/lighting/landing-lights")
		
	def checkTransparency(self, ob):
		is_transparent = False
		for slot in ob.material_slots:
			if slot.material and slot.material.use_transparency:
				is_transparent = True
				break
			
		if is_transparent:
			self.exp_anim.addTransparentObject(ob)