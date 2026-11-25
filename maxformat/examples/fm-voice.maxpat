{
	"patcher": {
		"fileversion": 1,
		"appversion": {
			"major": 8,
			"minor": 6,
			"revision": 5,
			"architecture": "x64",
			"modernui": 1
		},
		"classnamespace": "box",
		"rect": [
			80.0,
			80.0,
			934.0,
			1162.0
		],
		"bglocked": 0,
		"openinpresentation": 1,
		"default_fontsize": 12.0,
		"default_fontface": 0,
		"default_fontname": "Arial",
		"gridonopen": 1,
		"gridsize": [
			15.0,
			15.0
		],
		"gridsnaponopen": 1,
		"objectsnaponopen": 1,
		"statusbarvisible": 2,
		"toolbarvisible": 1,
		"lefttoolbarpinned": 0,
		"toptoolbarpinned": 0,
		"righttoolbarpinned": 0,
		"bottomtoolbarpinned": 0,
		"toolbars_unpinned_last_save": 0,
		"tallnewobj": 0,
		"boxanimatetime": 200,
		"enablehscroll": 1,
		"enablevscroll": 1,
		"devicewidth": 0.0,
		"description": "",
		"digest": "",
		"tags": "",
		"style": "",
		"subpatcher_template": "",
		"assistshowspatchername": 0,
		"boxes": [
			{
				"box": {
					"maxclass": "live.dial",
					"numinlets": 1,
					"numoutlets": 2,
					"outlettype": [
						"",
						"float"
					],
					"patching_rect": [
						30.0,
						225.0,
						44.0,
						48.0
					],
					"parameter_enable": 1,
					"varname": "Pitch",
					"saved_attribute_attributes": {
						"valueof": {
							"parameter_longname": "Pitch",
							"parameter_shortname": "Pitch",
							"parameter_type": 0,
							"parameter_mmin": 20,
							"parameter_mmax": 2000,
							"parameter_initial": [
								220
							],
							"parameter_initial_enable": 1,
							"parameter_unitstyle": 3
						}
					},
					"id": "obj-1",
					"presentation": 1,
					"presentation_rect": [
						15.0,
						97.5,
						44.0,
						48.0
					]
				}
			},
			{
				"box": {
					"maxclass": "live.dial",
					"numinlets": 1,
					"numoutlets": 2,
					"outlettype": [
						"",
						"float"
					],
					"patching_rect": [
						120.0,
						225.0,
						44.0,
						48.0
					],
					"parameter_enable": 1,
					"varname": "Ratio",
					"saved_attribute_attributes": {
						"valueof": {
							"parameter_longname": "Ratio",
							"parameter_shortname": "Ratio",
							"parameter_type": 0,
							"parameter_mmin": 0.25,
							"parameter_mmax": 8,
							"parameter_initial": [
								2
							],
							"parameter_initial_enable": 1,
							"parameter_unitstyle": 1
						}
					},
					"id": "obj-2",
					"presentation": 1,
					"presentation_rect": [
						75.0,
						97.5,
						44.0,
						48.0
					]
				}
			},
			{
				"box": {
					"maxclass": "live.dial",
					"numinlets": 1,
					"numoutlets": 2,
					"outlettype": [
						"",
						"float"
					],
					"patching_rect": [
						30.0,
						555.0,
						44.0,
						48.0
					],
					"parameter_enable": 1,
					"varname": "Index",
					"saved_attribute_attributes": {
						"valueof": {
							"parameter_longname": "Index",
							"parameter_shortname": "Index",
							"parameter_type": 0,
							"parameter_mmin": 0,
							"parameter_mmax": 20,
							"parameter_initial": [
								3
							],
							"parameter_initial_enable": 1,
							"parameter_unitstyle": 1
						}
					},
					"id": "obj-3",
					"presentation": 1,
					"presentation_rect": [
						135.0,
						97.5,
						44.0,
						48.0
					]
				}
			},
			{
				"box": {
					"maxclass": "newobj",
					"text": "* 1.",
					"numinlets": 2,
					"numoutlets": 1,
					"outlettype": [
						"float"
					],
					"patching_rect": [
						352.5,
						330.0,
						41.5,
						22.0
					],
					"id": "obj-4"
				}
			},
			{
				"box": {
					"maxclass": "newobj",
					"text": "sig~",
					"numinlets": 1,
					"numoutlets": 1,
					"outlettype": [
						"signal"
					],
					"patching_rect": [
						270.0,
						330.0,
						37.0,
						22.0
					],
					"id": "obj-5"
				}
			},
			{
				"box": {
					"maxclass": "newobj",
					"text": "*~ 2.",
					"numinlets": 2,
					"numoutlets": 1,
					"outlettype": [
						"signal"
					],
					"patching_rect": [
						292.5,
						405.0,
						49.0,
						22.0
					],
					"id": "obj-6"
				}
			},
			{
				"box": {
					"maxclass": "newobj",
					"text": "cycle~",
					"numinlets": 2,
					"numoutlets": 1,
					"outlettype": [
						"signal"
					],
					"patching_rect": [
						255.0,
						480.0,
						49.0,
						22.0
					],
					"id": "obj-7"
				}
			},
			{
				"box": {
					"maxclass": "newobj",
					"text": "*~",
					"numinlets": 2,
					"numoutlets": 1,
					"outlettype": [
						"signal"
					],
					"patching_rect": [
						285.0,
						555.0,
						34.0,
						22.0
					],
					"id": "obj-8"
				}
			},
			{
				"box": {
					"maxclass": "newobj",
					"text": "*~",
					"numinlets": 2,
					"numoutlets": 1,
					"outlettype": [
						"signal"
					],
					"patching_rect": [
						285.0,
						660.0,
						34.0,
						22.0
					],
					"id": "obj-9"
				}
			},
			{
				"box": {
					"maxclass": "newobj",
					"text": "+~",
					"numinlets": 2,
					"numoutlets": 1,
					"outlettype": [
						"signal"
					],
					"patching_rect": [
						645.0,
						735.0,
						34.0,
						22.0
					],
					"id": "obj-10"
				}
			},
			{
				"box": {
					"maxclass": "newobj",
					"text": "cycle~",
					"numinlets": 2,
					"numoutlets": 1,
					"outlettype": [
						"signal"
					],
					"patching_rect": [
						645.0,
						810.0,
						49.0,
						22.0
					],
					"id": "obj-11"
				}
			},
			{
				"box": {
					"maxclass": "live.gain~",
					"numinlets": 2,
					"numoutlets": 5,
					"outlettype": [
						"signal",
						"signal",
						"",
						"float",
						"list"
					],
					"patching_rect": [
						817.5,
						885.0,
						48.0,
						136.0
					],
					"parameter_enable": 1,
					"varname": "Level",
					"saved_attribute_attributes": {
						"valueof": {
							"parameter_longname": "Level",
							"parameter_shortname": "Level",
							"parameter_type": 0,
							"parameter_mmin": -70,
							"parameter_mmax": 6,
							"parameter_initial": [
								-12
							],
							"parameter_initial_enable": 1,
							"parameter_unitstyle": 4
						}
					},
					"id": "obj-12",
					"presentation": 1,
					"presentation_rect": [
						225.0,
						97.5,
						48.0,
						136.0
					]
				}
			},
			{
				"box": {
					"maxclass": "newobj",
					"text": "loadbang",
					"numinlets": 1,
					"numoutlets": 1,
					"outlettype": [
						"bang"
					],
					"patching_rect": [
						795.0,
						225.0,
						59.0,
						22.0
					],
					"id": "obj-13"
				}
			},
			{
				"box": {
					"maxclass": "message",
					"text": "startwindow",
					"numinlets": 2,
					"numoutlets": 1,
					"outlettype": [
						""
					],
					"patching_rect": [
						795.0,
						330.0,
						79.0,
						22.0
					],
					"id": "obj-14"
				}
			},
			{
				"box": {
					"maxclass": "newobj",
					"text": "dac~",
					"numinlets": 2,
					"numoutlets": 0,
					"outlettype": [],
					"patching_rect": [
						802.5,
						1080.0,
						41.5,
						22.0
					],
					"id": "obj-15"
				}
			},
			{
				"box": {
					"id": "obj-16",
					"maxclass": "comment",
					"text": "FM Voice",
					"numinlets": 1,
					"numoutlets": 0,
					"patching_rect": [
						30.0,
						30.0,
						95.0,
						27.6
					],
					"fontsize": 18.0,
					"fontface": 1,
					"presentation": 1,
					"presentation_rect": [
						15.0,
						15.0,
						95.0,
						27.6
					]
				}
			},
			{
				"box": {
					"id": "obj-17",
					"maxclass": "comment",
					"text": "v1.0",
					"numinlets": 1,
					"numoutlets": 0,
					"patching_rect": [
						133.0,
						36.0,
						43.0,
						20.4
					],
					"presentation": 1,
					"presentation_rect": [
						120.0,
						22.5,
						43.0,
						20.4
					]
				}
			},
			{
				"box": {
					"id": "obj-18",
					"maxclass": "comment",
					"text": "CONTROLS",
					"numinlets": 1,
					"numoutlets": 0,
					"patching_rect": [
						30.0,
						120.0,
						65.0,
						20.4
					],
					"fontface": 1,
					"presentation": 1,
					"presentation_rect": [
						15.0,
						67.5,
						65.0,
						20.4
					]
				}
			},
			{
				"box": {
					"id": "obj-19",
					"maxclass": "comment",
					"text": "MODULATOR",
					"numinlets": 1,
					"numoutlets": 0,
					"patching_rect": [
						255.0,
						120.0,
						71.0,
						20.4
					],
					"fontface": 1
				}
			},
			{
				"box": {
					"id": "obj-20",
					"maxclass": "comment",
					"text": "Turn up Index for brighter, more metallic tones.",
					"numinlets": 1,
					"numoutlets": 0,
					"patching_rect": [
						255.0,
						144.4,
						289.0,
						20.4
					]
				}
			},
			{
				"box": {
					"id": "obj-21",
					"maxclass": "comment",
					"text": "CARRIER",
					"numinlets": 1,
					"numoutlets": 0,
					"patching_rect": [
						645.0,
						120.0,
						60.0,
						20.4
					],
					"fontface": 1
				}
			},
			{
				"box": {
					"id": "obj-22",
					"maxclass": "comment",
					"text": "OUTPUT",
					"numinlets": 1,
					"numoutlets": 0,
					"patching_rect": [
						795.0,
						120.0,
						54.0,
						20.4
					],
					"fontface": 1,
					"presentation": 1,
					"presentation_rect": [
						225.0,
						67.5,
						54.0,
						20.4
					]
				}
			}
		],
		"lines": [
			{
				"patchline": {
					"source": [
						"obj-1",
						0
					],
					"destination": [
						"obj-5",
						0
					],
					"midpoints": [
						39.5,
						316.5,
						279.5,
						316.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-1",
						0
					],
					"destination": [
						"obj-4",
						0
					],
					"midpoints": [
						39.5,
						294.0,
						362.0,
						294.0
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-2",
						0
					],
					"destination": [
						"obj-4",
						1
					],
					"midpoints": [
						129.5,
						286.5,
						384.5,
						286.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-4",
						0
					],
					"destination": [
						"obj-6",
						1
					],
					"midpoints": [
						362.0,
						391.5,
						332.0,
						391.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-5",
						0
					],
					"destination": [
						"obj-6",
						0
					],
					"midpoints": [
						279.5,
						391.5,
						302.0,
						391.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-6",
						0
					],
					"destination": [
						"obj-7",
						0
					],
					"midpoints": [
						302.0,
						466.5,
						264.5,
						466.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-7",
						0
					],
					"destination": [
						"obj-8",
						0
					],
					"midpoints": [
						264.5,
						541.5,
						294.5,
						541.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-6",
						0
					],
					"destination": [
						"obj-8",
						1
					],
					"midpoints": [
						302.0,
						444.0,
						313.0,
						444.0,
						313.0,
						541.5,
						309.5,
						541.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-8",
						0
					],
					"destination": [
						"obj-9",
						0
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-3",
						0
					],
					"destination": [
						"obj-9",
						1
					],
					"midpoints": [
						39.5,
						646.5,
						309.5,
						646.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-9",
						0
					],
					"destination": [
						"obj-10",
						0
					],
					"midpoints": [
						294.5,
						721.5,
						654.5,
						721.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-5",
						0
					],
					"destination": [
						"obj-10",
						1
					],
					"midpoints": [
						279.5,
						369.0,
						669.5,
						369.0
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-10",
						0
					],
					"destination": [
						"obj-11",
						0
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-11",
						0
					],
					"destination": [
						"obj-12",
						0
					],
					"midpoints": [
						654.5,
						871.5,
						827.0,
						871.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-12",
						0
					],
					"destination": [
						"obj-15",
						0
					],
					"midpoints": [
						827.0,
						1044.0,
						812.0,
						1044.0
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-12",
						0
					],
					"destination": [
						"obj-15",
						1
					],
					"midpoints": [
						827.0,
						1066.5,
						834.5,
						1066.5
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-13",
						0
					],
					"destination": [
						"obj-14",
						0
					]
				}
			},
			{
				"patchline": {
					"source": [
						"obj-14",
						0
					],
					"destination": [
						"obj-15",
						0
					],
					"midpoints": [
						804.5,
						1044.0,
						812.0,
						1044.0
					]
				}
			}
		]
	}
}
