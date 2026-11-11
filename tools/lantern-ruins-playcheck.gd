extends SceneTree
## Independent play route. The only writes to the game are keyboard events.

const FRAME_RATE := 60
var game: Node
var held: Dictionary = {}
var capture_directory := ""

func _initialize() -> void:
	call_deferred("play")

func key(code: Key, pressed: bool) -> void:
	if bool(held.get(code, false)) == pressed:
		return
	held[code] = pressed
	var event := InputEventKey.new()
	event.keycode = code
	event.physical_keycode = code
	event.pressed = pressed
	Input.parse_input_event(event)

func frames(count: int) -> void:
	for frame in range(count):
		await process_frame

func tap(code: Key) -> void:
	key(code, true)
	await frames(2)
	key(code, false)
	await frames(2)

func check(condition: bool, label: String) -> bool:
	print("PLAY ", label, " ", "PASS" if condition else "FAIL",
		" state=", game.state, " room=", game.room, " time=", game.elapsed)
	if not condition:
		quit(1)
	return condition

func capture(label: String) -> void:
	if capture_directory.is_empty():
		return
	await RenderingServer.frame_post_draw
	var result := root.get_texture().get_image().save_png(capture_directory.path_join(label + ".png"))
	if result != OK:
		push_error("Failed to save screenshot: " + label)
		quit(2)

func move_to(target: Vector2) -> void:
	for frame in range(12 * FRAME_RATE):
		var delta: Vector2 = target - game.player.position
		if game.state != "play" or delta.length() < 5.0:
			break
		key(KEY_D, delta.x > 3)
		key(KEY_A, delta.x < -3)
		key(KEY_S, delta.y > 3)
		key(KEY_W, delta.y < -3)
		var dodge := false
		if delta.length() > 100.0 and game.player.cooldown <= 0.0:
			for hazard in game.hazard_positions:
				if game.player.position.distance_to(hazard) < 45.0:
					dodge = true
					break
		key(KEY_SPACE, dodge)
		await process_frame
	for code in [KEY_D, KEY_A, KEY_S, KEY_W, KEY_SPACE]:
		key(code, false)
	await frames(2)

func play() -> void:
	var arguments := OS.get_cmdline_user_args()
	if arguments.size() == 1:
		capture_directory = arguments[0]
	var scene := load("res://scenes/game.tscn") as PackedScene
	if scene == null or not scene.can_instantiate():
		quit(2)
		return
	game = scene.instantiate()
	if game.get("state") == null:
		quit(2)
		return
	root.add_child(game)
	await frames(2)
	if not check(game.state == "title" and not game.player.visible, "title"): return
	await capture("title")
	await tap(KEY_ENTER)
	if not check(game.state == "play" and "Relics 0/3" in game.hud.label.text, "start_hud"): return
	await capture("start-hud")
	key(KEY_A, true)
	await frames(90)
	key(KEY_A, false)
	if not check(game.player.position.x >= 32 and game.player.position.x < 38, "left_wall"): return
	await move_to(Vector2(480, 320))
	if not check(game.lantern_found and game.active and not game.relics[0], "light_and_shrine"): return
	await frames(18 * FRAME_RATE)
	if not check(game.state == "failure" and game.player.health == 0, "hazard_failure"): return
	await capture("failure")
	var result_player_hidden: bool = not game.player.visible
	await tap(KEY_R)
	if not check(game.state == "play" and game.room == 0 and game.player.health == 6
			and not game.lantern_found and game.relics == [false, false, false]
			and game.charge == [0.0, 0.0, 0.0] and game.player.visible, "restart"): return
	await capture("restart")
	for room in range(3):
		await move_to(Vector2(480, 320))
		if not check(game.room == room and game.active, "shrine_%d" % room): return
		await move_to(Vector2(480, 105))
		var corners := [Vector2(890, 105), Vector2(890, 560), Vector2(70, 560), Vector2(70, 105)]
		for lap in range(7):
			for corner in corners:
				if game.relics[room] or game.state != "play":
					break
				await move_to(corner)
			if game.relics[room] or game.state != "play":
				break
		if not check(game.state == "play" and game.relics[room], "earned_relic_%d" % room): return
		await move_to(Vector2(game.player.position.x, 320))
		await move_to(Vector2(480, 320))
		var charge: float = game.charge[room]
		await frames(120)
		if not check(not game.active and game.charge[room] == charge
				and game.relics.count(true) == room + 1, "no_duplicate_%d" % room): return
		key(KEY_D, true)
		await frames(225)
		key(KEY_D, false)
		if room < 2 and not check(game.state == "play" and game.room == room + 1, "east_door_%d" % room): return
	if not check(game.state == "victory" and game.relics.count(true) == 3, "victory"): return
	if not check(result_player_hidden and not game.player.visible, "result_player_hidden"): return
	await capture("victory")
	await tap(KEY_R)
	if not check(game.state == "play" and game.relics.count(true) == 0, "restart_after_victory"): return
	quit()
