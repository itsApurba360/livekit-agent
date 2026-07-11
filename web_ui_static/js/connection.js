// LiveKit room connection lifecycle: token fetch, connect/disconnect, event wiring
let currentRoom = null;

async function toggleConnection() {
    if (currentRoom && currentRoom.state === 'connected') {
        logMessage("info", "Disconnecting from call...");
        await disconnect();
    } else {
        await connect();
    }
}

async function connect() {
    const phoneInput = document.getElementById('phone_number').value.trim();
    const nameInput = document.getElementById('participant_name').value.trim() || 'Web Tester';
    const micDeviceId = document.getElementById('mic-select').value;
    const activeProfile = document.querySelector('.profile-btn.active')?.id.replace('profile-', '') || 'custom';

    if (!phoneInput || !phoneInput.match(/^\d+$/)) {
        logMessage("error", "Please provide a valid numeric caller phone number.");
        alert("Please enter a numeric phone number!");
        return;
    }

    const btn = document.getElementById('action-btn');
    const vInner = document.getElementById('v-inner');
    const vStatus = document.getElementById('v-status');
    const statusDot = document.getElementById('status-dot');
    const statusText = document.getElementById('status-text');

    btn.disabled = true;
    btn.innerHTML = 'Connecting...';
    vStatus.textContent = 'CONNECTING';
    vInner.className = 'visualizer-circle-inner connecting';

    // Generate room name starting with phone number so agent parses it
    const roomName = `${phoneInput}_web_test`;

    logMessage("info", `Fetching access token for room "${roomName}"...`);

    try {
        const response = await fetch('/api/token', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                room_name: roomName,
                phone_number: phoneInput,
                participant_identity: `web_${phoneInput}`,
                participant_name: nameInput,
                call_direction: activeProfile === 'outbound' ? 'outbound' : 'web',
                outbound_dial_mode: activeProfile === 'outbound' ? 'mock' : undefined,
                call_purpose: activeProfile === 'outbound' ? 'Local mock outbound call test' : undefined,
                requested_by: activeProfile === 'outbound' ? 'web_ui_mock' : undefined,
                agent_type: activeProfile === 'sales' || activeProfile === 'outbound' ? 'sales' : 'support'
            })
        });

        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.error || 'Failed to fetch token');
        }

        const data = await response.json();
        logMessage("success", "Token generated successfully.");
        logMessage("info", `LiveKit Cloud URL: ${data.server_url}`);

        // Initialize LiveKit Room
        currentRoom = new LivekitClient.Room({
            adaptiveStream: true,
            dynacast: true,
            publishDefaults: {
                audioPreset: LivekitClient.AudioPresets.speech,
            }
        });

        // Listen to room events
        currentRoom.on(LivekitClient.RoomEvent.Connected, () => {
            logMessage("success", "Connected to LiveKit room!");
            vStatus.textContent = 'CONNECTED';
            statusText.textContent = 'Connected';
            statusDot.className = 'badge-dot connected';
            btn.disabled = false;
            btn.classList.add('disconnect');
            btn.innerHTML = `
                <svg style="width: 20px; height: 20px;" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
                <span>End Call</span>
            `;
        });

        currentRoom.on(LivekitClient.RoomEvent.Disconnected, (reason) => {
            logMessage("warning", `Disconnected from room: ${reason || 'Normal Disconnect'}`);
            cleanupUI();
        });

        currentRoom.on(LivekitClient.RoomEvent.ParticipantConnected, (participant) => {
            logMessage("success", `Agent / Remote Participant joined: ${participant.identity}`);
            document.getElementById('v-name').textContent = participant.name || 'Agent';
            document.getElementById('v-avatar').textContent = '🤖';
        });

        currentRoom.on(LivekitClient.RoomEvent.ParticipantDisconnected, (participant) => {
            logMessage("warning", `Remote Participant left: ${participant.identity}`);
            document.getElementById('v-name').textContent = 'Call Tester';
            document.getElementById('v-avatar').textContent = '🎙️';
        });

        currentRoom.on(LivekitClient.RoomEvent.TrackSubscribed, (track, publication, participant) => {
            if (track.kind === 'audio') {
                logMessage("info", `Subscribed to audio track from ${participant.identity}`);
                const element = track.attach();
                element.id = `audio-${participant.identity}-${track.sid}`;
                document.body.appendChild(element);
            }
        });

        currentRoom.on(LivekitClient.RoomEvent.TrackUnsubscribed, (track, publication, participant) => {
            if (track.kind === 'audio') {
                logMessage("info", `Unsubscribed from audio track of ${participant.identity}`);
                const elements = track.detach();
                elements.forEach((el) => el.remove());
            }
        });

        // Connect to Room
        await currentRoom.connect(data.server_url, data.token);

        // Publish microphone
        logMessage("info", "Requesting microphone access with echo cancellation...");
        const audioCaptureOptions = {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        };
        if (micDeviceId) {
            audioCaptureOptions.deviceId = { exact: micDeviceId };
        }
        await currentRoom.localParticipant.setMicrophoneEnabled(true, audioCaptureOptions);
        logMessage("success", "Microphone published successfully.");

        // Start active audio level monitor
        startAudioMonitoring();

    } catch (err) {
        logMessage("error", `Connection failed: ${err.message}`);
        cleanupUI();
    }
}

async function disconnect() {
    if (currentRoom) {
        await currentRoom.disconnect();
    }
    cleanupUI();
}

function cleanupUI() {
    currentRoom = null;
    stopAudioMonitoring();

    // Clean up any remaining audio elements from the DOM
    document.querySelectorAll('audio').forEach((el) => el.remove());

    const btn = document.getElementById('action-btn');
    btn.disabled = false;
    btn.classList.remove('disconnect');
    btn.innerHTML = `
        <svg style="width: 20px; height: 20px;" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M19.114 5.636a9 9 0 010 12.728M16.463 8.288a5.25 5.25 0 010 7.424M6.75 8.25l4.72-4.72a.75.75 0 011.28.53v15.88a.75.75 0 01-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.01 9.01 0 012.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75z" />
        </svg>
        <span>Connect & Start Call</span>
    `;

    const vInner = document.getElementById('v-inner');
    const vStatus = document.getElementById('v-status');
    const statusDot = document.getElementById('status-dot');
    const statusText = document.getElementById('status-text');

    vStatus.textContent = 'OFFLINE';
    vInner.className = 'visualizer-circle-inner';
    vInner.style.transform = 'scale(1)';
    document.getElementById('visualizer-container').classList.remove('speaking', 'agent-speaking', 'user-speaking');

    statusText.textContent = 'Disconnected';
    statusDot.className = 'badge-dot';
    document.getElementById('v-name').textContent = 'Call Tester';
    document.getElementById('v-avatar').textContent = '🎙️';
}
