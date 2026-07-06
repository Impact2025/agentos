/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./frontend/index.html', './frontend/app.js'],
  theme: {
    extend: {
      colors: {
        // Behouden uit de oude inline-config (toekomstig gebruik)
        surface: { DEFAULT: '#131929', raised: '#1a2235', border: '#1e293b' },
      },
    },
  },
  // Vangnet: kleurfamilies die app.js dynamisch via lookup-objecten zet.
  // Ze staan al letterlijk in de broncode (en worden dus gescand), maar dit
  // garandeert dat een purge ze nooit per ongeluk weggooit.
  safelist: [
    { pattern: /(bg|text|border|ring)-(violet|amber|sky|emerald|indigo|red|slate)-(300|400|500|600|700|800|900|950)/ },
  ],
  plugins: [],
};
