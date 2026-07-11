// Profile switcher and page init
function selectProfile(type) {
    document.querySelectorAll('.profile-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById(`profile-${type}`).classList.add('active');

    const profile = PROFILES[type];
    const phoneInput = document.getElementById('phone_number');
    const nameInput = document.getElementById('participant_name');
    const expectedAgent = document.getElementById('expected-agent');
    const expectedEntity = document.getElementById('expected-entity');

    phoneInput.value = profile.phone;
    nameInput.value = profile.name;
    expectedAgent.textContent = profile.agent;
    expectedEntity.textContent = profile.entity;

    if (type === 'custom') {
        phoneInput.focus();
        phoneInput.removeAttribute('readonly');
        nameInput.removeAttribute('readonly');
    } else {
        phoneInput.setAttribute('readonly', true);
        nameInput.setAttribute('readonly', true);
    }
    logMessage("info", `Switched profile to: ${type.toUpperCase()}`);
}

// Prevent typing into Customer/Sales pre-defined fields by default
document.getElementById('phone_number').setAttribute('readonly', true);
document.getElementById('participant_name').setAttribute('readonly', true);
