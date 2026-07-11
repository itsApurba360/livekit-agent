// Visualizer state (idle/agent-speaking/user-speaking) and audio level polling
let audioMonitorFrame = null;
let lastVisualizerState = '';

function setVisualizerState(state, scale = 1) {
    const vInner = document.getElementById('v-inner');
    const vOuter = document.getElementById('v-outer');
    const vStatus = document.getElementById('v-status');
    const container = document.getElementById('visualizer-container');
    const stateKey = `${state}:${scale.toFixed(2)}`;

    if (stateKey === lastVisualizerState) return;
    lastVisualizerState = stateKey;

    container.classList.remove('speaking', 'agent-speaking', 'user-speaking');

    if (state === 'agent') {
        vInner.className = 'visualizer-circle-inner agent-speaking';
        vStatus.textContent = 'SPEAKING';
        container.classList.add('speaking', 'agent-speaking');
        vInner.style.transform = `scale(${scale})`;
        vOuter.style.borderColor = 'rgba(6, 182, 212, 0.35)';
    } else if (state === 'user') {
        vInner.className = 'visualizer-circle-inner user-speaking';
        vStatus.textContent = 'LISTENING';
        container.classList.add('speaking', 'user-speaking');
        vInner.style.transform = `scale(${scale})`;
        vOuter.style.borderColor = 'rgba(16, 185, 129, 0.35)';
    } else {
        vInner.className = 'visualizer-circle-inner';
        vStatus.textContent = 'CONNECTED';
        vInner.style.transform = 'scale(1)';
        vOuter.style.borderColor = 'rgba(255, 255, 255, 0.05)';
    }
}

function startAudioMonitoring() {
    let lastTick = 0;

    function tick(now) {
        if (!currentRoom || currentRoom.state !== 'connected') {
            audioMonitorFrame = null;
            return;
        }

        if (now - lastTick >= 100) {
            lastTick = now;

            let maxAgentLevel = 0;
            let agentSpeaking = false;

            currentRoom.remoteParticipants.forEach(p => {
                if (p.audioLevel > maxAgentLevel) maxAgentLevel = p.audioLevel;
                if (p.isSpeaking) agentSpeaking = true;
            });

            const userLevel = currentRoom.localParticipant?.audioLevel ?? 0;
            const userSpeaking = currentRoom.localParticipant?.isSpeaking ?? false;

            if (agentSpeaking && maxAgentLevel > 0.01) {
                setVisualizerState('agent', 1 + maxAgentLevel * 0.5);
            } else if (userSpeaking && userLevel > 0.01) {
                setVisualizerState('user', 1 + userLevel * 0.5);
            } else {
                setVisualizerState('idle');
            }
        }

        audioMonitorFrame = requestAnimationFrame(tick);
    }

    audioMonitorFrame = requestAnimationFrame(tick);
}

function stopAudioMonitoring() {
    if (audioMonitorFrame) {
        cancelAnimationFrame(audioMonitorFrame);
        audioMonitorFrame = null;
    }
    lastVisualizerState = '';
    document.getElementById('visualizer-container').classList.remove('speaking', 'agent-speaking', 'user-speaking');
}
