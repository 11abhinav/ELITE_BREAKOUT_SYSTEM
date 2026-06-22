  const savedTheme = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', savedTheme);

let _audioCtx = null;
function initAudio() {
    if (!_audioCtx) {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (AudioContext) _audioCtx = new AudioContext();
    }
    if (_audioCtx && _audioCtx.state === 'suspended') _audioCtx.resume();
}
document.addEventListener('click', initAudio, { once: true });

function playDing() {
    if (!_audioCtx) initAudio();
    if (!_audioCtx || _audioCtx.state === 'suspended') return;
    const osc = _audioCtx.createOscillator();
    const gain = _audioCtx.createGain();
    osc.connect(gain);
    gain.connect(_audioCtx.destination);
    osc.type = 'sine';
    osc.frequency.setValueAtTime(880, _audioCtx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(110, _audioCtx.currentTime + 0.5);
    gain.gain.setValueAtTime(0.3, _audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.01, _audioCtx.currentTime + 0.5);
    osc.start();
    osc.stop(_audioCtx.currentTime + 0.5);
}

