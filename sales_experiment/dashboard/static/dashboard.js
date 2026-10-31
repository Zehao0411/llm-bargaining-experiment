(() => {
  const form = document.getElementById('experiment-form');
  if (form) {
    const count = (name) => form.elements[name].value.split(',').filter(value => value.trim()).length;
    const update = () => {
      const repetitions = Number(form.elements.repetitions.value);
      const conditions = count('incentives') + Number(form.elements.include_high_price_no_incentive.checked);
      const total = Math.max(1, count('reference_prices')) * count('discounts') * conditions * repetitions;
      document.getElementById('trial-count').textContent = Number.isFinite(total) ? total.toLocaleString() : '—';
    };
    form.addEventListener('input', update);
    update();
    form.addEventListener('submit', () => {
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      button.textContent = 'Starting…';
    });
  }

  let refreshing = false;
  const refresh = async () => {
    if (document.hidden || refreshing || document.querySelector('select:focus')) return;
    const runs = document.getElementById('runs');
    const live = document.querySelectorAll('[data-live]');
    if (!runs && !live.length) return;
    refreshing = true;
    try {
      const response = await fetch(runs ? runs.dataset.runsUrl : window.location.href, {cache: 'no-store'});
      if (!response.ok) return;
      const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
      if (runs) {
        const replacement = parsed.getElementById('runs');
        if (replacement) runs.replaceWith(replacement);
      } else {
        const y = window.scrollY;
        const log = document.querySelector('pre.log');
        const logBottom = log && log.scrollHeight - log.scrollTop - log.clientHeight < 20;
        live.forEach(section => {
          const replacement = parsed.getElementById(section.id);
          if (replacement) section.replaceWith(replacement);
        });
        const freshLog = document.querySelector('pre.log');
        if (freshLog && logBottom) freshLog.scrollTop = freshLog.scrollHeight;
        window.scrollTo(0, y);
      }
    } catch (_) {
      // Retry on the next interval if the local server is temporarily unavailable.
    } finally {
      refreshing = false;
    }
  };
  setInterval(refresh, 8000);
})();
