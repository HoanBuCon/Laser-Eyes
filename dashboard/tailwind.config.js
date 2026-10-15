// Tailwind build for the dashboard pages that used the Play CDN (demo, calibration, index, review).
// The compiled file dashboard/css/tailwind-build.css is committed so the Hub works without Internet.
// Rebuild after adding Tailwind classes:
//   npx tailwindcss@3.4.19 -c dashboard/tailwind.config.js -i dashboard/css/tailwind-input.css -o dashboard/css/tailwind-build.css --minify
module.exports = {
  darkMode: 'class',
  content: ['./dashboard/*.html', './dashboard/js/*.js'],
  theme: {
    extend: {
      colors: {
        brand: { 500: '#06b6d4', 600: '#0891b2' },
        dark: { 950: '#070a12', 900: '#0b0f19', 800: '#111827', 700: '#1f2937' },
      },
    },
  },
};
