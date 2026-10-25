#version 330
precision highp float;
in vec2 in_pos;
out vec2 v_uv;
void main() {
    v_uv = in_pos;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
