function createNeuralRenderer(canvas) {
  const gl = canvas.getContext('webgl', {alpha:true, antialias:true, premultipliedAlpha:false});
  if (!gl) return null;
  function compile(type, source) {
    const shader = gl.createShader(type);
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return null;
    return shader;
  }
  const vertex = compile(gl.VERTEX_SHADER, `
    attribute vec3 point;
    attribute vec3 ring;
    attribute vec3 body;
    varying vec3 vRing;
    varying vec3 vBody;
    void main() {
      gl_Position = vec4(point.xy, 0.0, 1.0);
      gl_PointSize = point.z;
      vRing = ring;
      vBody = body;
    }
  `);
  const fragment = compile(gl.FRAGMENT_SHADER, `
    precision mediump float;
    varying vec3 vRing;
    varying vec3 vBody;
    void main() {
      vec2 xy = (gl_PointCoord - vec2(0.5)) * 2.0;
      float d = length(xy);
      if (d > 1.0) discard;
      float z = sqrt(max(0.0, 1.0 - d * d));
      float light = clamp(dot(vec3(xy, z), vec3(-0.35, -0.45, 0.8)), 0.0, 1.0);
      vec3 surface = vBody * (0.65 + light * 1.05);
      gl_FragColor = vec4(mix(surface, vRing, smoothstep(0.79, 0.88, d)),
        1.0 - smoothstep(0.94, 1.0, d));
    }
  `);
  if (!vertex || !fragment) return null;
  const program = gl.createProgram();
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return null;
  const buffer = gl.createBuffer();
  const names = ['point', 'ring', 'body'];
  const channels = color => [1, 3, 5].map(i => parseInt(color.slice(i, i + 2), 16) / 255);
  return function paint(points, width, height, cssWidth, cssHeight) {
    if (gl.isContextLost() || !cssWidth || !cssHeight) return false;
    const density = Math.min(devicePixelRatio || 1, 2, 4096 / cssWidth, 4096 / cssHeight);
    canvas.width = Math.max(1, Math.round(cssWidth * density));
    canvas.height = Math.max(1, Math.round(cssHeight * density));
    const size = canvas.width / width;
    const max = gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)[1];
    if (points.some(({radius}) => radius * 2 * size > max)) return false;
    const vertices = points.flatMap(({x, y, radius, ring, body}) => [
      x / width * 2 - 1, 1 - y / height * 2, radius * 2 * size,
      ...channels(ring), ...channels(body),
    ]);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(vertices), gl.DYNAMIC_DRAW);
    names.forEach((name, i) => {
      const index = gl.getAttribLocation(program, name);
      gl.enableVertexAttribArray(index);
      gl.vertexAttribPointer(index, 3, gl.FLOAT, false, 36, i * 12);
    });
    gl.drawArrays(gl.POINTS, 0, points.length);
    return true;
  };
}
