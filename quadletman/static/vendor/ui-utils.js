// Shared utilities for metrics sparkline rendering — used by dashboard.html and compartment_metrics.html

function drawSparkline(canvas, data, color) {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (data.length < 2) return;
  const max = Math.max(...data, 0.001);

  // Fill area under line
  ctx.beginPath();
  data.forEach((v, i) => {
    const x = (i / (data.length - 1)) * W;
    const y = H - (v / max) * H * 0.85 - 2;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.lineTo(W, H);
  ctx.lineTo(0, H);
  ctx.closePath();
  ctx.fillStyle = color + '22';
  ctx.fill();

  // Line
  ctx.beginPath();
  data.forEach((v, i) => {
    const x = (i / (data.length - 1)) * W;
    const y = H - (v / max) * H * 0.85 - 2;
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

function fmtBytes(b) {
  if (b >= 1e9) return (b / 1e9).toFixed(1) + ' GB';
  if (b >= 1e6) return (b / 1e6).toFixed(1) + ' MB';
  if (b >= 1e3) return (b / 1e3).toFixed(1) + ' KB';
  return b + ' B';
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

// Alpine component for the per-file permission editor in the volume browser.
// Receives the server-rendered mode object (ur/uw/ux/gr/gw/gx/or/ow/ox booleans)
// and exposes rwx checkboxes plus a computed octal getter for the hidden input.
function chmodEditor(mode) {
  return {
    showPerms: false,
    ur: mode.ur, uw: mode.uw, ux: mode.ux,
    gr: mode.gr, gw: mode.gw, gx: mode.gx,
    or_: mode.or, ow: mode.ow, ox: mode.ox,
    get octal() {
      const u = (this.ur ? 4 : 0) + (this.uw ? 2 : 0) + (this.ux ? 1 : 0);
      const g = (this.gr ? 4 : 0) + (this.gw ? 2 : 0) + (this.gx ? 1 : 0);
      const o = (this.or_ ? 4 : 0) + (this.ow ? 2 : 0) + (this.ox ? 1 : 0);
      return '' + u + g + o;
    },
  };
}
