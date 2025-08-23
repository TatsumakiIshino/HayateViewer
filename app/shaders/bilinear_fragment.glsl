#version 330 core

in vec2 TexCoord;
out vec4 FragColor;

uniform sampler2D textureSampler;
uniform vec2 sourceTextureSize; // 使わないが、texture()が線形補間を行う

void main() {
    // テクスチャのサンプラー設定で GL_LINEAR を指定することで、
    // ハードウェアによるバイリニア補間が自動的に行われる。
    FragColor = texture(textureSampler, TexCoord);
}