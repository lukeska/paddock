import Echo from 'laravel-echo';
import Pusher from 'pusher-js';

document.documentElement.dataset.paddockLab = 'ready';
window.Pusher = Pusher;

const publish = detail => {
  window.paddockReverbResult = detail;
  window.dispatchEvent(new CustomEvent('paddock-reverb-result', { detail }));
};

const token = crypto.randomUUID();
const echo = new Echo({
  broadcaster: 'reverb', key: 'paddock-lab', wsHost: location.hostname,
  wsPort: 443, wssPort: 443, forceTLS: true, enabledTransports: ['ws', 'wss'],
});
const timer = setTimeout(() => publish({ status: 'fail', details: 'Laravel Echo did not receive the broadcast within 8 seconds' }), 8000);
const channel = echo.channel('paddock-lab');
channel.listen('.PaddockLabEvent', event => {
  if (event.token !== token) return;
  clearTimeout(timer);
  publish({ status: 'pass', details: 'Laravel Echo received the uniquely tagged broadcast' });
  echo.leave('paddock-lab');
});
channel.subscribed(() => {
  fetch('/paddock/api/reverb?token=' + encodeURIComponent(token)).catch(error => {
    clearTimeout(timer);
    publish({ status: 'fail', details: error.message });
  });
});
