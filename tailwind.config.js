/** Scans every template and script for class names. Classes built by string
 * concatenation at runtime are not detected; write them out in full. */
module.exports = {
  content: ["./templates/**/*.html", "./static/js/*.js"],
  theme: { extend: {} },
  plugins: [],
};
