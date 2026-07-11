// Console log panel rendering
let logIndex = 1;

function logMessage(type, message) {
    const container = document.getElementById('console-logs');
    const entry = document.createElement('div');
    entry.className = 'log-entry';

    const now = new Date();
    const timeStr = now.toTimeString().split(' ')[0];

    entry.innerHTML = `<span class="log-time">[${timeStr}]</span> <span class="log-${type}">${message}</span>`;
    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;

    document.getElementById('log-counter').textContent = `${logIndex++} events`;
}
