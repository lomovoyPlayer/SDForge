// Силуэтная контрастная обводка для режима вращения
float rim = 1.0 - max(dot(normal, -rayDir), 0.0); //
float outline = smoothstep(0.4, 0.9, rim);

vec3 baseBody = vec3(0.1, 0.1, 0.1); // Тёмный силуэт
vec3 edgeColor = vec3(1.0, 0.5, 0.0);   // Контрастная обводка (оранжевая)

fragColor = vec4(mix(baseBody, edgeColor, outline), 1.0);
