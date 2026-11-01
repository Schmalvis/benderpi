// bender_config.json holds an LED colour as [r, g, b]. The page shows #rrggbb.

export function hexToRgb(hex) {
  const match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex ?? '');
  return match ? match.slice(1).map(part => parseInt(part, 16)) : null;
}

export function rgbToHex(rgb, fallback) {
  const valid = Array.isArray(rgb) && rgb.length === 3
    && rgb.every(n => Number.isInteger(n) && n >= 0 && n <= 255);
  return valid
    ? '#' + rgb.map(n => n.toString(16).padStart(2, '0')).join('')
    : fallback;
}
