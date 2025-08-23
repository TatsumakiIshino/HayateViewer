#version 330 core

in vec2 TexCoord;
out vec4 FragColor;

uniform sampler2D textureSampler;
uniform vec2 sourceTextureSize; // 使わないが、インターフェースを統一

void main() {
    FragColor = texture(textureSampler, TexCoord);
}