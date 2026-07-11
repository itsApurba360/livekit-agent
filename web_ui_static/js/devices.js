// Microphone input device enumeration
async function loadDevices() {
    try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const micSelect = document.getElementById('mic-select');
        micSelect.innerHTML = '<option value="">Default Microphone</option>';
        devices.forEach(device => {
            if (device.kind === 'audioinput') {
                const option = document.createElement('option');
                option.value = device.deviceId;
                option.text = device.label || `Microphone ${micSelect.length}`;
                micSelect.appendChild(option);
            }
        });
    } catch (err) {
        logMessage("warning", `Could not enumerate audio devices: ${err.message}`);
    }
}

navigator.mediaDevices.addEventListener('devicechange', loadDevices);
loadDevices();
