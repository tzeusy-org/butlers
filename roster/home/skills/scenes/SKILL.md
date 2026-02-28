# Skill: Scene Composition

## Purpose

Enable users to create, modify, and trigger scenes — bundled automations that execute multiple
device commands with a single voice/text trigger or scheduled execution. Scenes compose activities
like "Movie Night" (dim lights, close blinds, adjust sound) into named, reusable automations.

## When to Use

Use this skill when:
- User is creating a new scene ("Create a bedtime scene")
- User is modifying an existing scene ("Add window blinds to movie night")
- User is triggering/executing a scene ("Activate movie night")
- User is listing scenes or asking about scene status ("What scenes do I have?")
- User is deleting a scene ("Remove the workout scene")

## Workflow

### Step 1: Define Scene Composition (New Scene)

When user wants to create a new scene:

1. **Parse the scene intent**: Extract scene name and desired device states
   - Example: "Movie Night" → dim lights, close blinds, adjust sound
   - Example: "Bedtime" → cool bedroom, dim all lights, arm security

2. **Clarify missing details** via follow-up message:
   - Which devices should be included? (lights, blinds, thermostat, speakers, etc.)
   - What specific state for each device? (dim to 20%, close, set to 68°F, etc.)
   - Should it be triggered manually, scheduled, or both?

3. **Build device command list** from user's specifications:
   - Each device gets one command (turn on/off, dim, set mode, etc.) with parameters
   - Example: `[{"device": "living-room-light", "command": "dim", "brightness": 20}, {"device": "blinds", "command": "close"}, {"device": "sound-system", "command": "on"}]`

4. **Validate device availability**:
   - Check that specified devices exist and support requested commands
   - Alert if device doesn't exist: "I don't see a blinds device in your living room. Did you mean hallway blinds?"

5. **Call `scene_create()`** with:
   - `name`: Scene name (snake_case internally, e.g., "movie_night")
   - `description`: User-friendly description
   - `devices`: List of device commands from step 3

6. **Store scene preference** as memory fact:
   - `subject`: Scene name
   - `predicate`: `scene_preference` or `scene_created`
   - `content`: Description of devices and states
   - `permanence`: `standard` (scenes are long-lived unless explicitly changed)
   - `importance`: 6-7 (user explicitly defined this)
   - `tags`: Scene name, included devices, purpose (e.g., `["movie-night", "lights", "blinds", "entertainment"]`)

7. **Confirm with user** via `notify()` (affirm mode):
   - Repeat the scene composition back clearly
   - Example: "Movie Night scene created — lights dimmed to 20%, blinds closed, sound system on."
   - Offer optional enhancements: "Want me to schedule it for Friday evenings?"

### Step 2: Modify Existing Scene

When user wants to change an existing scene:

1. **Retrieve the scene** via `scene_get(scene_name)`
2. **Identify the change**:
   - Add device: "Also close the window blinds"
   - Remove device: "Never mind about the sound system"
   - Modify device state: "Make the lights brighter, 50% instead of 20%"

3. **Parse the modification**:
   - For additions: Add new device command to device list
   - For removals: Remove device command from list
   - For modifications: Update parameter for existing device

4. **Call `scene_update()`** with:
   - `name`: Scene name
   - `devices`: Updated device command list

5. **Update memory fact** to reflect modification

6. **Confirm via `notify()`** (affirm mode):
   - Example: "Updated Movie Night — now includes window blinds (will close with other devices)."

### Step 3: Execute Scene

When user triggers a scene:

1. **Retrieve the scene** via `scene_get(scene_name)` to confirm it exists
2. **Call `scene_execute(scene_name)`** to run all associated device commands
3. **Store execution** as volatile memory fact:
   - `subject`: Scene name
   - `predicate`: `usage_pattern`
   - `content`: "User triggered scene at [time]"
   - `permanence`: `volatile`
   - `tags`: Scene name, timestamp

4. **Confirm with user** via `notify()` (affirm mode):
   - List the devices being adjusted: "Scene 'Movie Night' activated — lights dimmed to 20%, blinds closed, sound system on."
   - Keep confirmation brief

### Step 4: Schedule Scene Execution

When user wants to automatically trigger a scene on a schedule:

1. **Parse the schedule**: "Run movie night at 7pm every Friday" → cron `0 19 * * 5`
2. **Create automation** via `automation_create()` with:
   - `name`: Descriptive automation name (e.g., "Friday Movie Night")
   - `trigger_type`: `schedule` (cron-based) or `manual`
   - `cron`: Cron expression (e.g., `0 19 * * 5`)
   - `action`: `scene_execute` with target scene name

3. **Confirm with user**:
   - "Scheduled 'Movie Night' to run at 7pm every Friday. Want me to send you a reminder 10 minutes before?"

4. **Store automation** as memory fact:
   - `subject`: Automation name
   - `predicate`: `automation_schedule`
   - `content`: Scene name, trigger time, frequency
   - `permanence`: `standard`
   - `tags`: Scene, schedule, frequency

### Step 5: List and Inspect Scenes

When user asks "What scenes do I have?" or similar:

1. **Call `scene_list()`** to get all defined scenes
2. **For each scene, call `scene_get(scene_name)`** to get details
3. **Compose response** via `notify()` (answer mode) with:
   - Scene names and descriptions
   - Devices included in each
   - Whether scene is manually triggered or scheduled
   - Example: "You have 3 scenes: Movie Night (lights, blinds, sound), Bedtime (thermostat, lights, security), Workout (lights, sound). Movie Night is scheduled for Fridays at 7pm."

### Step 6: Delete Scene

When user wants to remove a scene:

1. **Request confirmation** via `notify()` (follow-up mode):
   - "Delete 'Movie Night' scene? This cannot be undone."
   - Wait for ✅ reaction or explicit confirmation

2. **Call `scene_delete(scene_name)`** only after confirmation

3. **Confirm deletion** via `notify()`:
   - "Movie Night scene deleted."

4. **Clean up associated automations**:
   - If scene had scheduled automations, ask: "Also remove the Friday 7pm trigger?"

## Key Behaviors

### Scenes Are Device Bundles, Not Logic

Scenes execute a fixed set of device commands. They do not contain conditional logic or decision trees:
- Good: "Movie Night sets lights to 20%, closes blinds, turns on sound"
- Avoid: "Movie Night: if it's dark outside, dim lights; if someone is in the room, close blinds"

### One Scene Per Session

Create or modify one scene per user message. Don't try to create multiple scenes in a single interaction.

### Validate Device Commands

Before creating a scene, ensure:
- All requested devices exist in the home
- Each device supports the requested command (e.g., "turn on" for a light, "close" for blinds)
- Device commands have proper parameters (brightness 0-100, temperature in °F, etc.)

### Confirm Destructive Actions

Always ask for confirmation (✅ reaction) before deleting a scene or removing devices from a scene.

### Compose Scenes Incrementally

If user creates a scene with a few devices and later wants to add more, update incrementally:
- "Added window blinds to Movie Night. Want to add anything else?"
- Don't require user to re-specify all devices; just specify the new addition

### Remember Scene Patterns

Store usage patterns in memory. If user triggers Movie Night every Friday at 7pm, notice the pattern and offer scheduling:
- "I notice you activate Movie Night every Friday evening. Want me to schedule it automatically at 7pm?"

## Interactive Response Examples

### Scene Creation Flow

**User**: "Create a bedtime scene that cools the bedroom to 68 and dims all lights"

**Actions**:
1. `scene_create(name="bedtime", devices=[{"device": "bedroom-thermostat", "command": "set_temperature", "params": {"temperature": 68}}, {"device": "living-room-light", "command": "dim", "brightness": 10}, {"device": "bedroom-light", "command": "dim", "brightness": 0}])`
2. `memory_store_fact(subject="bedtime-scene", predicate="scene_created", ...)`
3. `notify(channel="telegram", message="Bedtime scene created — bedroom will cool to 68°F and all lights will dim. Want me to schedule it for 10pm every night?", intent="reply", request_context=...)`

### Scene Execution

**User**: "Activate movie night"

**Actions**:
1. `scene_get(scene_name="movie_night")`
2. `scene_execute(scene_name="movie_night")`
3. `memory_store_fact(subject="movie-night-scene", predicate="usage_pattern", ...)`
4. `notify(channel="telegram", message="Scene 'Movie Night' activated — lights dimmed to 20%, blinds closed, sound system on.", intent="reply", request_context=...)`

### Scene Listing

**User**: "What scenes do I have?"

**Actions**:
1. `scene_list()`
2. For each scene: `scene_get(scene_name)`
3. `notify(channel="telegram", message="You have 3 scenes:\n• Movie Night (lights, blinds, sound) — Fridays 7pm\n• Bedtime (thermostat, lights) — Daily 10pm\n• Workout (lights, music)", intent="reply", request_context=...)`

### Scene Deletion

**User**: "Delete the workout scene"

**Actions**:
1. `notify(channel="telegram", message="Delete 'Workout' scene? This cannot be undone. React ✅ to confirm.", intent="reply", request_context=...)`
2. Wait for ✅
3. `scene_delete(scene_name="workout")`
4. `notify(channel="telegram", message="Workout scene deleted.", intent="reply", request_context=...)`

### Multi-Turn Scene Composition (HA Tools)

This example uses the native Home Assistant tools available to the home butler. It illustrates
how to handle an iterative, multi-turn scene-building conversation using `ha_*` tools directly.

**Turn 1 — User**: "I want to create a relaxation scene"

**Actions**:
1. Ask for clarification via `notify(channel="telegram", message="What devices should the relaxation
   scene include? For example: lighting, temperature, blinds, music?", intent="reply", request_context=...)`

**Turn 2 — User**: "Dim the lights to 30%, play soft music, and set temperature to 72"

**Actions**:
1. `ha_list_entities(domain="light")` — discover all light entity IDs
2. `ha_call_service(domain="light", service="turn_on", target={"area_id": "all"}, data={"brightness_pct": 30})`
3. `ha_call_service(domain="media_player", service="play_media", target={"entity_id": "media_player.living_room"}, data={"media_content_id": "soft_music", "media_content_type": "music"})`
4. `ha_call_service(domain="climate", service="set_temperature", data={"temperature": 72})`
5. `notify(channel="telegram", message="Relaxation mode active — lights at 30%, soft music on, temperature set to 72°F. Anything else to add?", intent="reply", request_context=...)`

**Turn 3 — User**: "Actually, also close the blinds"

**Actions**:
1. `ha_list_entities(domain="cover")` — find blind/cover entity IDs
2. `ha_call_service(domain="cover", service="close_cover", target={"area_id": "all"})`
3. `ha_activate_scene(entity_id="scene.relaxation")` if scene already exists, or:
   `ha_call_service(domain="scene", service="create", data={"scene_id": "relaxation", "entities": {"light.living_room": {"state": "on", "brightness": 77}, "media_player.living_room": {"state": "playing"}, "climate.main": {"temperature": 72}, "cover.living_room": {"state": "closed"}}})`
4. `memory_store_fact(subject="relaxation-scene", predicate="scene_preference", content="relaxation scene includes: lights at 30%, soft music, 72°F temperature, closed blinds", permanence="standard", importance=6.0, tags=["scene", "relaxation", "automation"])`
5. `notify(channel="telegram", message="Relaxation scene saved — lights at 30%, soft music playing, 72°F, blinds closed. Activate it anytime by saying 'activate relaxation'.", intent="reply", request_context=...)`

## Exit Criteria

- For scene creation: `scene_create()` was called; user confirmed via `notify()`
- For scene modification: `scene_update()` was called; user confirmed
- For scene execution: `scene_execute()` was called; user confirmed
- For scheduling: `automation_create()` was called; automation registered
- For listing: `scene_list()` and `scene_get()` calls completed; user received list
- For deletion: `scene_delete()` was called after user confirmation
- `memory_store_fact()` was called to persist scene definition or usage pattern
- Session exits without starting new workflow

## Common Failure Modes and Recovery

### User Specifies Device That Doesn't Exist
- Alert: "I don't see a 'ceiling fan' in your living room. Did you mean bedroom ceiling fan?"
- Offer alternatives if device exists elsewhere
- Do not create scene with non-existent device reference

### User Wants Conditional Logic in Scene
- Explain limitation: "Scenes execute the same commands every time. If you want different behavior based on time of day, I can create separate scenes."
- Example: "Workout Morning" (bright lights 8am-6pm) vs. "Workout Evening" (normal lights 6pm-10pm)

### User Creates Scene with Conflicting Device States
- Example: User creates scene that sets thermostat to 68°F AND turns off heating
- Alert: "Bedtime sets temperature to 68°F but also turns off heating. These conflict. Should we remove one?"

### User Forgets Scene Name or Parameters
- Retrieve scene via `scene_get()` and remind user
- Offer to show scene details before executing
