#version 330 
precision highp float;
precision highp sampler2DArray;
// --- Входные данные и выходные данные ---
// Вершинные данные, переданные из вершинного шейдера
in vec2 v_uv;
// Выходные данные - цвет пикселя
out vec4 fragColor;

// --- Параметры ---
#define PI 3.14159265358979323846

// --- Структуры ---

struct Node
{
    // Каждый узел это операция и фигура опциональна, так как может быть просто операция над другими узлами без своей фигуры
    int operationType;  // для операций: 0 - объединение, 1 - пересечение, 2 - вычитание ... 
                                                        // Ожидается, что значение типа всегда будет больше или равно 0, потому как без операции узла быть не может
    int figureType; // для фигур: -1 - нет, 0 - сфера, 1 - коробка, 2 - капсула и т.д. 
                
    vec2 params; // указывает на позицию и размер данных в буффере данных из 2D текстуры
    
};

// Сдвиги в текстуре для доступа к параметрам операции

#define NODE_ID_SHIFT_OPERATION_ARGUMENT_A_REGISTER_ID 0
#define NODE_OPERATION_ARGUMENT_A_REGISTER_ID(paramsStart) int(texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_OPERATION_ARGUMENT_A_REGISTER_ID, u_TBPWidth), 0).x)
#define NODE_OPERATION_ARGUMENT_A(paramsStart) readRegister(NODE_OPERATION_ARGUMENT_A_REGISTER_ID(paramsStart))
#define NODE_ID_SHIFT_OPERATION_ARGUMENT_B_REGISTER_ID 0
#define NODE_OPERATION_ARGUMENT_B_REGISTER_ID(paramsStart) int(texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_OPERATION_ARGUMENT_B_REGISTER_ID, u_TBPWidth), 0).y)
#define NODE_OPERATION_ARGUMENT_B(paramsStart) readRegister(NODE_OPERATION_ARGUMENT_B_REGISTER_ID(paramsStart))
#define NODE_ID_SHIFT_OPERATION_ARGUMENT_C 0 // Может быть как радиус сглаживания у soft операций, размер фаски у chamber операций, так и радиусом скругления или толщиной стенки 
#define NODE_OPERATION_ARGUMENT_C(paramsStart) texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_OPERATION_ARGUMENT_C, u_TBPWidth), 0).z
#define NODE_ID_SHIFT_OPERATION_ARGUMENT_D 0 // Дополнительный аргумент для некоторых операций, если вдруг понадобится в будущем
#define NODE_OPERATION_ARGUMENT_D(paramsStart) texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_OPERATION_ARGUMENT_D, u_TBPWidth), 0).w

// Сдвиги в текстуре для доступа к параметрам фигуры прикреплённой за операцией (if figureExist == true)

#define NODE_ID_SHIFT_ALBEDO_AND_MATERIAL 1
#define NODE_ALBEDO(paramsStart) texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_ALBEDO_AND_MATERIAL, u_TBPWidth), 0).rgb
#define NODE_MATERIAL_ID(paramsStart) int(texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_ALBEDO_AND_MATERIAL, u_TBPWidth), 0).w)

#define NODE_ID_SHIFT_TRANSLATION_AND_REGISTER_ID 2
#define NODE_TRANSLATION(paramsStart) texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_TRANSLATION_AND_REGISTER_ID, u_TBPWidth), 0).xyz
#define NODE_REGISTER_ID(paramsStart) int(texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_TRANSLATION_AND_REGISTER_ID, u_TBPWidth), 0).w)

#define NODE_ID_SHIFT_ROTATION 3
#define NODE_ROTATION(paramsStart) texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_ROTATION, u_TBPWidth), 0).xyz

#define NODE_ID_SHIFT_ELONGATION_AND_SCALE 4
#define NODE_ELONGATION(paramsStart) texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_ELONGATION_AND_SCALE, u_TBPWidth), 0).xyz
#define NODE_SCALE(paramsStart) texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_ELONGATION_AND_SCALE, u_TBPWidth), 0).w

#define NODE_ID_SHIFT_TEXTURES_ID 5
#define NODE_TEXTURES_ID(paramsStart) texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_TEXTURES_ID, u_TBPWidth), 0).xyz



// SUBTYPE для операций
// БИНАРНУЕ ОПЕРАЦИИ
#define OPERATION_SUBTYPE_UNION 0
#define OPERATION_SUBTYPE_SUBTRACT 1
#define OPERATION_SUBTYPE_INTERSECT 2
#define OPERATION_SUBTYPE_SMOOTH_UNION 3
#define OPERATION_SUBTYPE_SMOOTH_SUBTRACT 4
#define OPERATION_SUBTYPE_SMOOTH_INTERSECT 5
#define OPERATION_SUBTYPE_CHAMBER_UNION 6
#define OPERATION_SUBTYPE_CHAMBER_SUBTRACT 7
#define OPERATION_SUBTYPE_CHAMBER_INTERSECT 8
// УНИТАРНЫЕ ОПЕРАЦИИ
#define OPERATION_SUBTYPE_ROUND 9
#define OPERATION_SUBTYPE_SHELL 10



// Значения материалов
struct Material
{
    float roughness; // Матовость материала, регулирует радиус конуса Cone Tracing отражений
    float metallic; // Металличность материала
    float transmission; // Прозрачность материала (для рендера стекла и других прозрачных материалов)
    float subsurface; // Глубина подповерхностного рассеивания
    vec3 sssColor; // Цвет подповерхностного рассеивания
    float ior; // Показатель преломления
    float emission; // Светимость материала (для рендера светящихся объектов)
};

// --- Юниформы ---

// Камера
uniform vec2 u_resolution; // Полное разрешение итогового изображения в пикселях (например, 1920x1080).
// uniform int u_samples; // Кол-во сэмплов для сглаживания
    // использование семплов отменяется, потому как планируется Cone Tracing / Cone Stepping
uniform float u_fovYDeg;
uniform vec3 u_camPos;
uniform vec3 u_camRot;

//Свет
#define LIGHT_MAX_COUNT 16 // Максимальное кол-во источников света в сцене
const int LIGHT_EXTRA_RAY_MAX = 8; // Максимум доп. лучей площадной выборки на один источник (u_areaRayCount обрезается здесь)
uniform int u_lightCount;
uniform int u_lights[LIGHT_MAX_COUNT]; // Список источников света в сцене, каждый элемент - это индекс нода-фигуры, 
                                        // который содержит в своих параметрах информацию об источнике света
// Художественный масштаб мощности всех источников.
// Компенсирует расхождение между яркостью источника на экране
// (сжатой ACES) и его линейной осветительной силой
uniform float u_LightPowerScale;

uniform vec3 u_ambientLightColor;
uniform float u_ambientLightIntensity;

uniform int u_showFloor;
uniform int u_transparentBackground; 

// Параметры отражений
uniform int u_maxReflectCount;
uniform bool u_physGlass; // Физическое стекло - включает преломление и поглощение света внутри материала

// Площадная выборка источников
uniform int u_areaRayCount;   // доп. лучи к вершинам n-угольника; < 3 — только центральный луч
uniform int u_topLightCount;  // сколько самых мощных источников получают доп. лучи (макс. 4)
uniform float u_ngonPhase;    // азимутальный поворот n-угольника выборки, радианы

// Накопление сэмплов и дисперсия
uniform int u_sampleSeed;     // сид джиттера кадра; 0 — эталон (центр пикселя)
uniform int u_sampleCount;    // всего кадров накопления M; 0/1 — без страт
uniform int u_sampleId;       // номер текущего кадра семпла
uniform float u_dispersion;   // сила аналитической дисперсии 0..1
uniform bool u_outputLinear;  // линейный вывод без ACES — для накопления на хосте

//Стекло
#define GLASS_MAX_COUNT 32 // Максимальное кол-во стеклянных объектов в сцене
uniform int u_glassCount;
uniform int u_glasses[GLASS_MAX_COUNT]; // Список стеклянных объектов в сцене, каждый элемент - это индекс нода-фигуры, 
                                        // Это предвычесленный на cpu список стеклянных объектов,
                                        // Нужно для оперативной фильтрации стеклянных объектов при трассировке лучей, 
                                        //чтобы не проверять каждый узел на стекло, а сразу проверять только стеклянные объекты
// Быстрый режим рендера
uniform bool u_fastMode;

// Texture Buffer Params (TBP) - для передачи данных о сцене в виде текстуры
uniform sampler2D u_TBP; // Текстура, содержащая данные о сцене
uniform int u_TBPWidth; // Ширина текстуры буфера для перевода одномерной координаты в двумерную
                            // Обращаться к элементу текстуры через texelFetch

// Список материалов
#define MATERIALS_LIST_MAX_LENGTH 64 //128 // Кол-во материалов в списке материалов
uniform int u_materialsCount;
uniform Material u_materialsList[MATERIALS_LIST_MAX_LENGTH]; // Список материалов, используемых в сцене

uniform highp sampler2DArray u_textures; // Список всех используемых текстур, 
                                    // Ожидается, что рендерер будет сжимать текстуры до максимального размера, 
                                    // а те что меньше будут без рескейлинга, просто в левом верхнем углу с педдингом

//Nodes
#define NODES_MAX_LENGTH 128 // 64 //общая длинна последовательности узлов/команд
uniform int u_nodesCount;
uniform Node u_nodes[NODES_MAX_LENGTH];

// Время, момент запуска шейдера, может быть использовано для анимации или динамических эффектов
uniform float u_time;

// --- Константы ---

// Константные параметры для карт высот
const float depthOffset = 0.5; 
const float blendSharpness = 8.0; 

// Константные параметры отражений
const int CONE_MAX_REFRACT = 6;       // первичные лучи: стакан с водой — 4 преломления, запас на вложенность
const int CONE_MAX_REFRACT_CHEAP = 2; // вторичные лучи: преломление там почти не читается
const float DISPERSION_COEFF = 0.04; // подобрать позже подходящий

// Грейс-зона для ложных приповерхностных хитов в lightVisibilityCone:
// self-node-блокеры ближе этой дистанции игнорируются (консервативная поправка SDF/рельеф).
const float SELF_HIT_GRACE_DIST = 0.05;

// Минимальный шаг внутри своего SSS-тела при проходе к источнику:
// больше 0.005, чтобы толстые тела не сжигали бюджет итераций, но не слишком
// большой, чтобы хорда для SSS оставалась приемлемо точной.
const float SSS_SELF_PASS_MIN_STEP = 0.02;
// Грейс для любого блокера на малых дистанциях (0.0 — выключено; включать только если
// декодирование покажет, что лучи умирают на CSG-партнёре прямо у поверхности).
const float ALL_HIT_GRACE_DIST = 0.0;

// Временный debug-визуализатор для SSS-блоков: R — id блокера, G — дистанция блока,
// B — gVisFail. 0 — обычный рендер, 1 — чистый debug-оверлей.
#define SSS_DEBUG_VIS 0

// --- Параметры неба и атмосферы ---
// Весь блок — константы с пометкой "→ uniform": кандидаты на перепрофилирование в юниформы.
const int   SKY_RAY_MAX           = 12;     // 3 солнечных луча + до 9 кольцевых (массив)
uniform float u_skySunAzimuthDeg;       //  азимут светила
uniform float u_skySunElevationDeg;     //  высота светила над горизонтом
uniform float u_skySunAngRadius;        //  угловой радиус диска, рад
const float SKY_SUN_GLOW          = 0.02;   // → uniform; угловая ширина внешнего сияния
const float SKY_SUN_GLOW_INNER    = 0.010;  // → uniform; угол узкого яркого обода диска
const float SKY_SUN_DISC_EXPOSURE = 10.0;   // → uniform; усиление диска светила (подбирается визуально)
uniform float u_skyAirDensity;          //  плотность чистого воздуха (зенитный σ)
const float SKY_HAZE_DENSITY      = 0.02;   // → uniform; плотность приземной дымки (σ на метр)
const float SKY_HAZE_HORIZON      = 0.0;    // → uniform; высотное смещение дымки (зарезервировано)
const float SKY_HG_G              = 0.6;    // → uniform; асимметрия фазы Хеньи-Гринштейна
const float SKY_GROUND_BOUNCE     = 0.25;   // → uniform; доля неба, отражённая землёй в нижнюю полусферу

// Дистанции и апертуры маршей видимости неба.
const float SKY_VIS_DIST_SUN      = 30.0;   // → uniform; бюджет луча к светилу
const float SKY_VIS_DIST_RING     = 12.0;   // → uniform; бюджет кольцевых лучей
const float SKY_CONE_RING         = 0.20;   // → uniform; раскрытие кольцевых лучей

// Калибровка вклада неба в освещение поверхности (→ uniform).
const int   SKY_SUN_MULTI_MIN_RAYS = 6;     // минимум лучей неба для треугольной выборки светила
const float SKY_SUN_TRIANGLE_RADIUS = 0.6;  // → uniform; радиус треугольника в долях SKY_SUN_ANG_RADIUS
const float SKY_AMBIENT_SCALE     = 2.0;    // вес кольцевых лучей относительно солнечного
const float SKY_RING_SOFT_ANGLE   = 0.15;   // → uniform; угловая ширина мягкого терминатора кольца
const float SKY_SHADOW_SOFTNESS   = 0.5;    // → uniform; смягчение небесных теней (0 = чёткие, 1 = нет теней)

// --- Регистры ---
// Регистр для хранения промежуточных результатов в процессе вычисления SDF
vec2 reg0, reg1, reg2, reg3, reg4, reg5, reg6, reg7;

// --- Функции ---

// Макрос или функция для записи (вместо registers[id] = res). 
// Видеокарта не может хранить массив регистров при динамичесом индексе в регистрах и отправляет в vram, что замедляет в 10-100 раз
// Поэтому используем 8 отдельных переменных и функцию для записи в них, что позволяет избежать этой проблемы и ускорить рендер
void writeRegister(int id, vec2 val) {
    if (id == 0) reg0 = val;
    else if (id == 1) reg1 = val;
    else if (id == 2) reg2 = val;
    else if (id == 3) reg3 = val;
    else if (id == 4) reg4 = val;
    else if (id == 5) reg5 = val;
    else if (id == 6) reg6 = val;
    else if (id == 7) reg7 = val;
}

// Макрос или функция для чтения
vec2 readRegister(int id) {
    if (id == 0) return reg0;
    if (id == 1) return reg1;
    if (id == 2) return reg2;
    if (id == 3) return reg3;
    if (id == 4) return reg4;
    if (id == 5) return reg5;
    if (id == 6) return reg6;
    if (id == 7) return reg7;
    return vec2(0.0);
}

ivec2 indexToCoords(int index, int width)
{
    int x = index % width;
    int y = index / width;
    return ivec2(x, y);
}

void getNodeAlbedoAndMaterialId(int paramsStart, out vec3 albedo, out int materialId) 
{
    vec4 albedoMaterialData = texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_ALBEDO_AND_MATERIAL, u_TBPWidth), 0);
    albedo = albedoMaterialData.rgb;
    materialId = int(albedoMaterialData.w);
}

void getNodeTranslationAndRegisterID(int paramsStart, out vec3 translation, out int registerID)
{
    vec4 translationRegisterData = texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_TRANSLATION_AND_REGISTER_ID, u_TBPWidth), 0);
    translation = translationRegisterData.xyz;
    registerID = int(translationRegisterData.w);
}

void getNodeElongationAndScale(int paramsStart, out vec3 elongation, out float scale) 
{
    vec4 elongationScaleData = texelFetch(u_TBP, indexToCoords(paramsStart + NODE_ID_SHIFT_ELONGATION_AND_SCALE, u_TBPWidth), 0);
    elongation = elongationScaleData.xyz;
    scale = elongationScaleData.w;
}

ivec2 getTextureInfo(int paramsStart, int texType)
{
    vec3 IDInfo = NODE_TEXTURES_ID(paramsStart);
    int texID;
    float rawData;
    int texParamsIdx;
    if(texType == 0)  rawData = IDInfo.x; // Альбедо
    if(texType == 1)  rawData = IDInfo.y; // Карта нормалей
    if(texType == 2)  rawData = IDInfo.z; // Карта высот
    if (rawData >= 0.0) 
    {
        texID = int(mod(rawData, 100.0));
        texParamsIdx = int(floor(rawData / 100.0));
    } 
    else 
    {
        texID = -1;
        texParamsIdx = -1;
    }


    return ivec2(texID, texParamsIdx);
}

bool loadNodeTextureParams(
    int paramsStart,
    out int albedoTexID,
    out vec2 texOffset,
    out float texRotation,
    out vec2 texOriginalSize,
    out float texTilingScale
) {
    ivec2 texInfo = getTextureInfo(paramsStart, 0);
    albedoTexID = texInfo.x;

    if (albedoTexID == -1) {
        return false;
    }

    int texParamsIdx = texInfo.y;
    vec3 texParams = texelFetch(u_TBP, indexToCoords(paramsStart + 4 + texParamsIdx * 2, u_TBPWidth), 0).xyz;

    vec3 texSizeScale = texelFetch(u_TBP, indexToCoords(paramsStart + 4 + texParamsIdx * 2 + 1, u_TBPWidth), 0).xyz;

    texOffset = texParams.xy;
    texRotation = texParams.z;
    texOriginalSize = texSizeScale.xy;
    texTilingScale = texSizeScale.z;

    return true;
}

bool loadNodeNormalMapParams(
    int paramsStart,
    out int normalMapTexID,
    out vec2 normalMapOffset,
    out float normalMapRotation,
    out vec2 normalMapOriginalSize,
    out float normalMapTilingScale
) 
{
    ivec2 normalMapInfo = getTextureInfo(paramsStart, 1);
    normalMapTexID = normalMapInfo.x;

    if (normalMapTexID == -1) {
        return false;
    }

    int normalMapParamsIdx = normalMapInfo.y;
    vec3 normalMapParams = texelFetch(u_TBP, indexToCoords(paramsStart + 4 + normalMapParamsIdx*2, u_TBPWidth), 0).xyz;
    vec3 normalMapSizeScale = texelFetch(u_TBP, indexToCoords(paramsStart + 4 + normalMapParamsIdx*2 + 1, u_TBPWidth), 0).xyz;

    normalMapOffset = normalMapParams.xy;
    normalMapRotation = normalMapParams.z;
    normalMapOriginalSize = normalMapSizeScale.xy;
    normalMapTilingScale = normalMapSizeScale.z;

    return true;
}

bool loadNodeHeightMapParams(
    int paramsStart,
    out int heightMapTexID,
    out vec2 heightMapOffset,
    out float heightMapRotation,
    out vec2 heightMapOriginalSize,
    out float heightMapTilingScale,
    out float heightMapDepthScale
) 
{
    ivec2 heightMapInfo = getTextureInfo(paramsStart, 2);
    heightMapTexID = heightMapInfo.x;
    if (heightMapTexID == -1) {
        return false;
    }

    int heightMapParamsIdx = heightMapInfo.y;
    vec3 heightMapParams = texelFetch(u_TBP, indexToCoords(paramsStart + 4 + heightMapParamsIdx * 2, u_TBPWidth), 0).xyz;
    vec4 heightMapSizeScale = texelFetch(u_TBP, indexToCoords(paramsStart + 4 + heightMapParamsIdx * 2 + 1, u_TBPWidth), 0).xyzw;

    heightMapOffset = heightMapParams.xy;
    heightMapRotation = heightMapParams.z;
    heightMapOriginalSize = heightMapSizeScale.xy;
    heightMapTilingScale = heightMapSizeScale.z;
    heightMapDepthScale = heightMapSizeScale.w;

    return true;
}

int getSpecialParamsStart(int paramsStart) {
    vec3 IDInfo = NODE_TEXTURES_ID(paramsStart);
    int maxIdShift = int(floor(max(max(IDInfo.x, IDInfo.y), IDInfo.z) / 100.0 + 0.01));
    return paramsStart + NODE_ID_SHIFT_TEXTURES_ID + 1 + maxIdShift * 2;
}

// Плавное объединение (Smooth Min)
// k - радиус сглаживания (например, 0.1)
float smin(float a, float b, float k) {
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

// Плавное вычитание (Smooth Max)
// k - радиус сглаживания
float smax(float a, float b, float k) {
    float h = clamp(0.5 - 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) + k * h * (1.0 - h);
}

// Функция для генерации псевдослучайного числа на основе координат
float rand(vec2 p2)
{
    vec3 p3  = fract(vec3(p2.xyx) * .1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}
// Функция для генерации случайной точки на сфере
vec3 randomSpherePoint(vec2 seed) {
    float z = rand(seed) * 2.0 - 1.0;
    float a = rand(seed + vec2(1.23, 4.56)) * 6.2831853;
    float r = sqrt(1.0 - z * z);
    return vec3(r * cos(a), r * sin(a), z);
}

mat2 rotate(float angle) {
    float s = sin(angle);
    float c = cos(angle);
    return mat2(c, -s, s, c);
}

vec3 rotate3D(vec3 p, vec3 angles) {
    p.yz = rotate(angles.x) * p.yz;
    p.xz = rotate(angles.y) * p.xz;
    p.xy = rotate(angles.z) * p.xy;
    return p;
}

vec3 inverseRotate3D(vec3 p, vec3 angles) {
    p.xy = rotate(-angles.z) * p.xy;
    p.xz = rotate(-angles.y) * p.xz;
    p.yz = rotate(-angles.x) * p.yz;
    return p;
}

vec2 calculateUV(
    sampler2DArray texArray,
    vec2 localPos2D, 
    vec2 origTexSize, 
    vec2 texOffset, 
    float texRotation, 
    float tilingScale
) {
    vec2 pos = localPos2D * tilingScale;
    pos = rotate(-texRotation) * pos - texOffset;
    
    vec2 wrappedUV = fract(pos);
    vec2 arraySize = vec2(textureSize(texArray, 0).xy);
    vec2 atlasScale = origTexSize / arraySize;
    
    return wrappedUV * atlasScale; 
}

vec3 sampleSDFTexture(
    sampler2DArray texArray, 
    float layerIndex,
    vec3 worldPos, 
    vec2 origTexSize, 
    vec2 texOffset, 
    float texRotation, 
    float tilingScale, 
    vec3 objOffset, 
    vec3 objRotation, 
    float radiusUV
) {
    vec3 localPos = inverseRotate3D(worldPos - objOffset, objRotation);
    vec2 localPos2D = localPos.xy; 

    if (radiusUV <= 0.001) {
        vec2 uv = calculateUV(texArray, localPos2D, origTexSize, texOffset, texRotation, tilingScale);
        return textureLod(texArray, vec3(uv, layerIndex), 0.0).rgb;
    }
    
    vec3 resultColor = vec3(0.0);
    
    vec2 offsets[5] = vec2[](
        vec2(0.0, 0.0),                  
        vec2(0.866025, 0.5),             
        vec2(-0.866025, 0.5),            
        vec2(0.866025, -0.5),            
        vec2(-0.866025, -0.5)
    );
    
    for (int i = 0; i < 5; i++) {
        vec2 offsetPos2D = localPos2D + offsets[i] * radiusUV;
        vec2 uv = calculateUV(texArray, offsetPos2D, origTexSize, texOffset, texRotation, tilingScale);
        resultColor += textureLod(texArray, vec3(uv, layerIndex), 0.0).rgb;
    }
    
    return resultColor * 0.2;
}

// ----------------------------------------------------------------------
// Трипланарное наложение текстур
//
// UV-проекции (calculateUV):
//   uvX = f(localPos.zy) — плоскость YZ, нормальная ось X, u↔Y, v↔Z
//   uvY = f(localPos.xz) — плоскость XZ, нормальная ось Y, u↔X, v↔Z
//   uvZ = f(localPos.xy) — плоскость XY, нормальная ось Z, u↔X, v↔Y
// ----------------------------------------------------------------------

vec3 getTriplanarWeights(vec3 normal, float sharpness) {
    vec3 w = pow(abs(normal), vec3(sharpness));
    return w / (w.x + w.y + w.z);   // normal нормирована → сумма > 0 всегда
}

vec3 triplanarColor(sampler2DArray texArray, float layerIndex,
                    vec3 worldPos, vec3 worldNormal,
                    vec2 origTexSize, vec2 texOffset,
                    float texRotation, float tilingScale,
                    vec3 objOffset, vec3 objRotation, float sharpness) {
    vec3 localNormal = inverseRotate3D(worldNormal, objRotation);
    vec3 localPos = inverseRotate3D(worldPos - objOffset, objRotation);
    vec3 blendWeights = getTriplanarWeights(localNormal, sharpness);

    vec2 uvX = calculateUV(texArray, localPos.zy, origTexSize, texOffset, texRotation, tilingScale);
    vec2 uvY = calculateUV(texArray, localPos.xz, origTexSize, texOffset, texRotation, tilingScale);
    vec2 uvZ = calculateUV(texArray, localPos.xy, origTexSize, texOffset, texRotation, tilingScale);

    vec3 colX = textureLod(texArray, vec3(uvX, layerIndex), 0.0).rgb;
    vec3 colY = textureLod(texArray, vec3(uvY, layerIndex), 0.0).rgb;
    vec3 colZ = textureLod(texArray, vec3(uvZ, layerIndex), 0.0).rgb;

    return colX * blendWeights.x + colY * blendWeights.y + colZ * blendWeights.z;
}

vec3 triplanarNormal(sampler2DArray texArray, float layerIndex,
                     vec3 worldPos, vec3 worldNormal,
                     vec2 origTexSize, vec2 texOffset,
                     float texRotation, float tilingScale,
                     vec3 objOffset, vec3 objRotation, float sharpness) {
    vec3 localNormal = inverseRotate3D(worldNormal, objRotation);
    vec3 localPos = inverseRotate3D(worldPos - objOffset, objRotation);
    vec3 blendWeights = getTriplanarWeights(localNormal, sharpness);

    vec2 uvX = calculateUV(texArray, localPos.zy, origTexSize, texOffset, texRotation, tilingScale);
    vec2 uvY = calculateUV(texArray, localPos.xz, origTexSize, texOffset, texRotation, tilingScale);
    vec2 uvZ = calculateUV(texArray, localPos.xy, origTexSize, texOffset, texRotation, tilingScale);

    vec3 tNormalX = textureLod(texArray, vec3(uvX, layerIndex), 0.0).xyz * 2.0 - 1.0;
    vec3 tNormalY = textureLod(texArray, vec3(uvY, layerIndex), 0.0).xyz * 2.0 - 1.0;
    vec3 tNormalZ = textureLod(texArray, vec3(uvZ, layerIndex), 0.0).xyz * 2.0 - 1.0;

    // Разворот касательной нормали в локальное пространство объекта:
    // tN.z (из поверхности) -> нормальная ось плоскости со знаком,
    // tN.x -> ось плоскости, давшая u;  tN.y -> ось, давшая v.
    // sign(0)=0 безопасен: при нулевой компоненте нормали вес проекции тоже 0.
    vec3 axisSign = sign(localNormal);

    vec3 finalNormalX = vec3(tNormalX.z * axisSign.x, tNormalX.y, tNormalX.x); // плоскость YZ
    vec3 finalNormalY = vec3(tNormalY.x, tNormalY.z * axisSign.y, tNormalY.y); // плоскость XZ
    vec3 finalNormalZ = vec3(tNormalZ.x, tNormalZ.y, tNormalZ.z * axisSign.z); // плоскость XY

    vec3 blendedTangentNormal = finalNormalX * blendWeights.x
                              + finalNormalY * blendWeights.y
                              + finalNormalZ * blendWeights.z;

    vec3 localResNormal = normalize(localNormal + blendedTangentNormal);
    return normalize(rotate3D(localResNormal, objRotation));
}

float triplanarHeight(sampler2DArray texArray, float layerIndex,
                      vec3 worldPos, vec3 worldNormal,
                      vec2 origTexSize, vec2 texOffset,
                      float texRotation, float tilingScale,
                      vec3 objOffset, vec3 objRotation,
                      float sharpness, float depthScale, float depthOffset) {
    vec3 localNormal = inverseRotate3D(worldNormal, objRotation);
    vec3 localPos = inverseRotate3D(worldPos - objOffset, objRotation);
    vec3 blendWeights = getTriplanarWeights(localNormal, sharpness);

    vec2 uvX = calculateUV(texArray, localPos.zy, origTexSize, texOffset, texRotation, tilingScale);
    vec2 uvY = calculateUV(texArray, localPos.xz, origTexSize, texOffset, texRotation, tilingScale);
    vec2 uvZ = calculateUV(texArray, localPos.xy, origTexSize, texOffset, texRotation, tilingScale);

    float hX = textureLod(texArray, vec3(uvX, layerIndex), 0.0).r;
    float hY = textureLod(texArray, vec3(uvY, layerIndex), 0.0).r;
    float hZ = textureLod(texArray, vec3(uvZ, layerIndex), 0.0).r;

    float finalHeight = hX * blendWeights.x + hY * blendWeights.y + hZ * blendWeights.z;
    return (finalHeight - depthOffset) * depthScale;
}

// Перевод из RGB в HSV
vec3 rgb2hsv(vec3 c) {
    vec4 K = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
    vec4 p = mix(vec4(c.bg, K.wz), vec4(c.gb, K.xy), step(c.b, c.g));
    vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
    float d = q.x - min(q.w, q.y);
    float e = 1.0e-10;
    return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + e)), d / (q.x + e), q.x);
}

// Перевод из HSV в RGB
vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0 / 3.0, 1.0 / 3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

// Гибридное наложение:
// texColor - исходный цвет из текстуры
// targetColor - цвет (альбедо) объекта
// ч/б текстура (насыщенность < 0.05) — колоризация цветом объекта;
// цветная текстура — сдвиг тона, взвешенный насыщенностью цвета объекта,
// плюс модуляция яркости для тёмных оттенков.
vec3 ApplyHueOrColorize(vec3 texColor, vec3 targetColor) {
    vec3 hsvTex = rgb2hsv(texColor);
    vec3 hsvTarget = rgb2hsv(targetColor);

    float isColorMask = smoothstep(0.05, 0.15, hsvTex.y);

    // Цветная текстура.
    // Сдвиг тона умножается на насыщенность цвета объекта:
    // нейтральный (белый/серый) объект оставляет текстуру как есть,
    // насыщенный — поворачивает тон на величину своего hue.
    vec3 shiftedHSV = hsvTex;
    shiftedHSV.x = fract(shiftedHSV.x + hsvTarget.x * hsvTarget.y);
    // Насыщенность текстуры ослабляется не более чем вдвое —
    // цвет объекта тонирует, а не обесцвечивает
    shiftedHSV.y *= 0.5 + 0.5 * hsvTarget.y;
    // Яркость объекта модулирует яркость текстуры:
    // тёмный объект затемняет текстуру, светлый почти не меняет
    shiftedHSV.z *= hsvTarget.z;
    vec3 shiftedRGB = hsv2rgb(shiftedHSV);

    // Ч/б текстура: колоризация без изменений —
    // тон и насыщенность от объекта, яркость от текстуры
    vec3 colorizedHSV = vec3(hsvTarget.x, hsvTarget.y, hsvTex.z);
    vec3 colorizedRGB = hsv2rgb(colorizedHSV);

    return mix(colorizedRGB, shiftedRGB, isColorMask);
}

// Код этой функции будет сгенерировван внешним кодом
float evalSDF(vec3 p, int sdfType, int paramsStart);

// ----------------------------------------------------------------------
// Приблизительная оценка размера фигуры узла
//
// Фигура аппроксимируется вписанным эллипсоидом:
// полуоси = радиус вписанной сферы базовой фигуры
// (расстояние от центра фигуры до ближайшей точки её поверхности)
// плюс компоненты вытягивания вдоль каждой оси, всё в мировом масштабе.
// Проекция эллипсоида на плоскость, перпендикулярную лучу, — эллипс,
// вокруг которого строится описанный прямоугольник, ориентированный
// относительно направления луча.
//
// Принятые упрощения:
// - позиция фигуры совпадает с её геометрическим центром;
// - вытянутые и масштабированные варианты фигур не имеют отдельных типов,
//   форма всегда восстанавливается как базовая фигура + вытягивание + масштаб;
// - оценка является нижней границей размера: углы вытянутых вдоль диагонали
//   коробок и подобных фигур выступают за пределы вписанного эллипсоида;
// - фигуры с полостью в центре (тор и т.п.) дают вырожденный нулевой радиус,
//   для них оценка сводится к одному вытягиванию.
//
// ----------------------------------------------------------------------

// Полуоси вписанного эллипсоида в мировых единицах (без учёта поворота).
// Фигура считается вписанной в единичный куб: полуоси задаются
// вытягиванием покомпонентно, мировой масштаб — модулем scale
// (размер неотрицателен даже при отражающем масштабе)
vec3 getNodeInscribedSemiAxes(int nodeIndex, int paramsStart)
{
    vec3 elongation;
    float scale;
    getNodeElongationAndScale(paramsStart, elongation, scale);
    return max(elongation, vec3(0.001)) * abs(scale);
}

// Ортонормированный базис (axisU, axisV) в плоскости,
// перпендикулярной направлению луча.
// axisU строится от мировой вертикали, чтобы ориентация
// прямоугольника была стабильной при малых изменениях луча.
void buildRayBasis(vec3 rayDir, out vec3 axisU, out vec3 axisV)
{
    // Опорный вектор выбирается наименее параллельным лучу,
    // чтобы векторное произведение не вырождалось
    vec3 up = abs(rayDir.y) < 0.99 ? vec3(0.0, 1.0, 0.0) : vec3(1.0, 0.0, 0.0);
    axisU = normalize(cross(up, rayDir));
    axisV = cross(rayDir, axisU);
}

// Максимальная полуось вписанного эллипсоида.
// Быстрая скалярная оценка размера без привязки к направлению луча.
// Не является радиусом описанной сферы: угловатые фигуры
// выступают за пределы вписанного эллипсоида.
float getNodeApproxMaxSemiAxis(int nodeIndex)
{
    int paramsStart = int(u_nodes[nodeIndex].params.x);
    vec3 semi = getNodeInscribedSemiAxes(nodeIndex, paramsStart);
    return max(semi.x, max(semi.y, semi.z));
}

// Функция для расчета нормали к поверхности на основе SDF
vec3 calcNormalBase(vec3 p, int sdfType, int paramsStart) {
    vec2 e = vec2(1.0, -1.0) * 0.01; 
    return normalize(
        e.xyy * evalSDF(p + e.xyy, sdfType, paramsStart) + 
        e.yyx * evalSDF(p + e.yyx, sdfType, paramsStart) +
        e.yxy * evalSDF(p + e.yxy, sdfType, paramsStart) +
        e.xxx * evalSDF(p + e.xxx, sdfType, paramsStart)
    );
}

// Основная функция для получения SDF сцены, которая будет использоваться для рендеринга и освещения
void initRegisters()
{
    const float FAR_DIST = 1e20;
    vec2 emptyVal = vec2(FAR_DIST, -1.0);

    reg0 = emptyVal;
    reg1 = emptyVal;
    reg2 = emptyVal;
    reg3 = emptyVal;
    reg4 = emptyVal;
    reg5 = emptyVal;
    reg6 = emptyVal;
    reg7 = emptyVal;
}

vec2 evalNodeSDF(vec3 worldPoint, int nodeIndex, int paramsStart)
{
    vec3 translation = NODE_TRANSLATION(paramsStart);
    vec3 rotation = NODE_ROTATION(paramsStart);
    vec3 elongation;
    float scale;
    getNodeElongationAndScale(paramsStart, elongation, scale);

    vec3 p_local = worldPoint - translation;
    p_local = inverseRotate3D(p_local, rotation);
    p_local /= scale;

    // elongation — гибридная семантика по каждой оси:
    //   > 1 — вытягивание (сфера -> капсула)
    //   = 1 — без изменений
    //   < 1 — сжатие (сфера -> сплюснутый эллипсоид)
    vec3 add = max(elongation - vec3(1.0), vec3(0.0));         // вытягивание
    vec3 sq  = max(min(elongation, vec3(1.0)), vec3(0.001));   // сжатие + защита от 0

    vec3 p_el = p_local - clamp(p_local, -add, add);           // сначала вытягивание
    vec3 p_sq = p_el / sq;                                     // затем сжатие

    float dist = evalSDF(p_sq, u_nodes[nodeIndex].figureType, paramsStart);
    dist *= min(min(sq.x, sq.y), sq.z);  // консервативная поправка (только за сжатие)
    dist *= scale;

    int hightTexID;
    vec2 hightMapOffset;
    float hightMapRotation;
    vec2 hightMapOriginalSize;
    float hightMapTilingScale;
    float hightMapDepthScale;

    bool hightMapExists = loadNodeHeightMapParams(
        paramsStart,
        hightTexID,
        hightMapOffset,
        hightMapRotation,
        hightMapOriginalSize,
        hightMapTilingScale,
        hightMapDepthScale
    );

    // ×0 — настоящий обход: без него в полосе 0.01 остаётся halving
    // дистанции, который пертурбирует ведомость зондов у поверхности
    if (hightMapExists && abs(hightMapDepthScale) > 1e-4 && dist < abs(hightMapDepthScale) + 0.01)
    {
        vec3 nLocal = calcNormalBase(p_sq, u_nodes[nodeIndex].figureType, paramsStart);
        nLocal = normalize(nLocal / sq);  // градиент трансформируется обратно-транспонированно
        vec3 n = rotate3D(nLocal, rotation);

        float height = triplanarHeight(
            u_textures,
            float(hightTexID),
            worldPoint,
            n,
            hightMapOriginalSize,
            hightMapOffset,
            hightMapRotation,
            hightMapTilingScale,
            translation,
            rotation,
            blendSharpness,
            hightMapDepthScale,
            depthOffset
        );

        dist -= height;
        dist *= 0.5;
    }

    vec2 figureRes = vec2(dist, float(nodeIndex));

    return figureRes;
}

vec2 operation(int paramsStart, int nodeIndex)
{
    vec2 a = NODE_OPERATION_ARGUMENT_A(paramsStart);
    vec2 b = NODE_OPERATION_ARGUMENT_B(paramsStart);
    vec2 opRes = a;
    //opRes.y - NodeId ближайшего узла
    //opRes.x - дистанция до ближайшей точки
    switch (u_nodes[nodeIndex].operationType)
    {
        case OPERATION_SUBTYPE_UNION:
        {
            opRes.y = (a.x < b.x) ? a.y : b.y;
            opRes.x = min(a.x, b.x);
            break;
        }

        case OPERATION_SUBTYPE_SUBTRACT:
        {
            opRes.y = a.y;
            opRes.x = max(a.x, -b.x);
            break;
        }

        case OPERATION_SUBTYPE_INTERSECT:
        {
            opRes.y = a.y;
            opRes.x = max(a.x, b.x);
            break;
        }

        case OPERATION_SUBTYPE_SMOOTH_UNION:
        {
            opRes.y = (a.x < b.x) ? a.y : b.y;
            opRes.x = smin(a.x, b.x, NODE_OPERATION_ARGUMENT_C(paramsStart));
            break;
        }

        case OPERATION_SUBTYPE_SMOOTH_SUBTRACT:
        {
            opRes.y = a.y;
            opRes.x = smax(a.x, -b.x, NODE_OPERATION_ARGUMENT_C(paramsStart));
            break;
        }

        case OPERATION_SUBTYPE_SMOOTH_INTERSECT:
        {
            opRes.y = a.y;
            opRes.x = smax(a.x, b.x, NODE_OPERATION_ARGUMENT_C(paramsStart));
            break;
        }

        case OPERATION_SUBTYPE_CHAMBER_UNION:
        {
            float r = NODE_OPERATION_ARGUMENT_C(paramsStart);
            opRes.y = (a.x < b.x) ? a.y : b.y;
            opRes.x = min(min(a.x, b.x), (a.x - r + b.x) * sqrt(0.5));
            break;
        }

        case OPERATION_SUBTYPE_CHAMBER_SUBTRACT:
        {
            float r = NODE_OPERATION_ARGUMENT_C(paramsStart);
            opRes.y = a.y;
            opRes.x = max(max(a.x, -b.x), (a.x + r - b.x) * sqrt(0.5));
            break;
        }

        case OPERATION_SUBTYPE_CHAMBER_INTERSECT:
        {
            float r = NODE_OPERATION_ARGUMENT_C(paramsStart);
            opRes.y = a.y;
            opRes.x = max(max(a.x, b.x), (a.x + r + b.x) * sqrt(0.5));
            break;
        }

        case OPERATION_SUBTYPE_ROUND:
        {
            float r = NODE_OPERATION_ARGUMENT_C(paramsStart);
            opRes.y = a.y;
            opRes.x = a.x - r;
            break;
        }

        case OPERATION_SUBTYPE_SHELL:
        {
            float thickness = NODE_OPERATION_ARGUMENT_C(paramsStart);
            opRes.y = a.y;
            opRes.x = abs(a.x) - thickness;
            break;
        }

        default:
        {
            opRes = a;
            break;
        }

    }
    writeRegister(NODE_OPERATION_ARGUMENT_A_REGISTER_ID(paramsStart), opRes);
    return opRes;
}

vec2 mapBase(vec3 point)
{
    initRegisters();

    vec2 lastRes = vec2(1e20, -1.0);

    for (int i = 0; i < u_nodesCount; i++)
    {
        int paramsStart = int(u_nodes[i].params.x);
        if (u_nodes[i].figureType != -1)
        {
            vec2 figureRes = evalNodeSDF(point, i, paramsStart);
            writeRegister(NODE_REGISTER_ID(paramsStart), figureRes);
        }
        lastRes = operation(paramsStart, i);
    }

    return lastRes;
}

// SDF только по источникам света (union по u_lights).
vec2 mapLight(vec3 p)
{
    vec2 res = vec2(1e20, -1.0);
    int count = min(u_lightCount, LIGHT_MAX_COUNT);
    for (int i = 0; i < count; i++)
    {
        int nodeId = u_lights[i];
        if (nodeId < 0 || nodeId >= u_nodesCount)
            continue;
        int paramsStart = int(u_nodes[nodeId].params.x);
        vec2 r = evalNodeSDF(p, nodeId, paramsStart);
        if (r.x < res.x)
            res = r;
    }
    return res;
}

bool isLightNode(int nodeIndex)
{
    for (int i = 0; i < u_lightCount; i++)
    {
        if (u_lights[i] == nodeIndex) return true;
    }
    return false;
}

bool isGlassNode(int nodeIndex)
{
    int paramsStart = int(u_nodes[nodeIndex].params.x);
    int materialId = NODE_MATERIAL_ID(paramsStart);
    Material mat = u_materialsList[materialId];
    return mat.transmission > 0.001;
}

// SDF сцены для теней:
// источники света и прозрачные объекты не блокируют свет,
// но их регистры всё равно заполняются "пустотой", чтобы CSG оставался корректным.
vec2 mapShadow(vec3 point)
{
    const float FAR_DIST = 1e20;

    initRegisters();

    vec2 lastRes = vec2(FAR_DIST, -1.0);

    for (int i = 0; i < u_nodesCount; i++)
    {
        int paramsStart = int(u_nodes[i].params.x);

        if (u_nodes[i].figureType != -1)
        {
            bool ignoreForShadow = isLightNode(i) || isGlassNode(i);

            if (ignoreForShadow)
            {
                writeRegister(
                    NODE_REGISTER_ID(paramsStart),
                    vec2(FAR_DIST, -1.0)
                );
            }
            else
            {
                vec2 figureRes = evalNodeSDF(point, i, paramsStart);
                writeRegister(NODE_REGISTER_ID(paramsStart), figureRes);
            }
        }

        lastRes = operation(paramsStart, i);
    }

    return lastRes;
}

// Толщина тела за точкой поверхности в направлении источников света.
// mapShadow — намеренно: игнорирует источники света и стекло.
float probeThickness(vec3 p, vec3 dir, float maxLen)
{
    float t = 0.02;
    float dPrev = -1.0;
    for (int i = 0; i < 8; i++)
    {
        float d = mapShadow(p + dir * t).x;
        if (d > 0.0)
        {
            // Секущая между последним внутренним и первым наружным
            // сэмплом: снимает квант шага на выходе из тела
            if (dPrev < 0.0)
            {
                float stepBack = max(-dPrev, 0.005);
                t -= stepBack * d / (d - dPrev);
            }
            return t;
        }
        dPrev = d;
        t += max(-d, 0.005);         // внутри SDF отрицателен: шагаем на |d|
        if (t > maxLen) break;
    }
    return t;
}

// Глубина пути луча сквозь светящееся тело (любой формы)
float probeLightThickness(vec3 p, vec3 dir, float maxLen)
{
    float t = 0.02;
    for (int i = 0; i < 8; i++)
    {
        float d = mapLight(p + dir * t).x;
        if (d > 0.0) break;
        t += max(-d, 0.005);
        if (t > maxLen) break;
    }
    return t;
}

// Контракт достижения геометрии за этот пиксель: точки первичного,
// отражённого и теневого контактов. Небо видимо только если луч
// выбивается из этого раствора. AABB известных точек — не доказательство
// замкнутости: через щель между двумя домами луч «внутри» рамки.
vec3 gReachMin;
vec3 gReachMax;

int gVisFail; // ОТЛАДКА: причина нулевой видимости светового марша
int gVisBlocker; // ОТЛАДКА: какой узел заблокировал марш
float gVisBlockT; // ОТЛАДКА: расстояние, на котором марш заблокирован

void noteReach(vec3 p)
{
    gReachMin = min(gReachMin, p);
    gReachMax = max(gReachMax, p);
}

// ---------------------------------------------------------------------
// Трекинг ореолов источников света.
// Ореол считается по собственному SDF источника, а не по ближайшему
// узлу сцены: squash-поправка minSq занижает дистанцию сжатых фигур,
// и плоская фигура рядом с источником перехватывает слот ближайшего
// узла в огромной зоне, разрывая трекинг. Собственное поле иммунно.
// ---------------------------------------------------------------------
#define GLOW_TRACK_SLOTS 4 // сколько ореолов отслеживается одновременно

// Кэш данных источников, заполняется раз на пиксель
float haloVoidR[LIGHT_MAX_COUNT]; // радиус пустоты источника (0 — сплошная фигура)
int haloNode[LIGHT_MAX_COUNT];
int haloParams[LIGHT_MAX_COUNT];
vec3 haloPos[LIGHT_MAX_COUNT];
float haloBound[LIGHT_MAX_COUNT];
float haloMinSq[LIGHT_MAX_COUNT];
int haloCount;

// Слоты трекинга: перигей каждого активного источника
int slotNode[GLOW_TRACK_SLOTS];
float slotMinDist[GLOW_TRACK_SLOTS];
float slotMinRadius[GLOW_TRACK_SLOTS];
bool slotSeen[GLOW_TRACK_SLOTS];

// Свечение одного источника по данным перигея скольжения конуса:
// доля перекрытия конуса в ближайшей точке сближения с поверхностью.
// Каналы считаются со слегка разными радиусами — хроматическая
// кайма ореола, признак линзового рассеяния
vec3 grazingGlowColor(int nodeId, float minDist, float minRadius)
{
    int nps = int(u_nodes[nodeId].params.x);
    vec3 gAlbedo; int gMatId;
    getNodeAlbedoAndMaterialId(nps, gAlbedo, gMatId);
    Material gMat = u_materialsList[gMatId];

    // Компенсация squash: dist из evalNodeSDF занижен на minSq
    vec3 gElong; float gScale;
    getNodeElongationAndScale(nps, gElong, gScale);
    vec3 gSq = max(min(gElong, vec3(1.0)), vec3(0.001));
    float minSq = min(min(gSq.x, gSq.y), gSq.z);

    float q = clamp(minDist / (max(minRadius, 1e-4) * minSq), 0.0, 1.0);
    vec3 sig = vec3(0.40, 0.36, 0.32);
    vec3 g = exp(-0.5 * q * q / (sig * sig));
    vec3 gEdge = exp(-0.5 / (sig * sig));
    vec3 chromatic = clamp((g - gEdge) / (1.0 - gEdge), vec3(0.0), vec3(1.0));
    float intensity = smoothstep(0.10, 2.00, gMat.emission);
    float glowPower = min(gMat.emission * 0.40, 4.00);
    return gAlbedo * glowPower * chromatic * intensity;
}

// Раз на пиксель: центры, радиусы описанных сфер, squash-поправки, радиусы пустот
void initHaloLights()
{
    haloCount = min(u_lightCount, LIGHT_MAX_COUNT);
    for (int li = 0; li < LIGHT_MAX_COUNT; li++)
    {
        if (li >= haloCount) break;
        int ln = u_lights[li];
        haloNode[li] = -1; haloParams[li] = 0; haloPos[li] = vec3(0.0);
        haloBound[li] = 0.0; haloMinSq[li] = 1.0; haloVoidR[li] = 0.0;
        if (ln < 0 || ln >= u_nodesCount) continue;
        int lps = int(u_nodes[ln].params.x);
        haloNode[li] = ln;
        haloParams[li] = lps;
        haloPos[li] = NODE_TRANSLATION(lps);
        haloBound[li] = getNodeApproxMaxSemiAxis(ln);
        vec3 lElong; float lScale;
        getNodeElongationAndScale(lps, lElong, lScale);
        vec3 lSq = max(min(lElong, vec3(1.0)), vec3(0.001));
        haloMinSq[li] = min(min(lSq.x, lSq.y), lSq.z);

        // Дистанция из центра фигуры до её поверхности: > 0 — центр снаружи,
        // фигура полая (тор, shell и т.п.), значение — радиус пустоты
        // в единицах базовой фигуры (куб с полуосью 1)
        if (u_nodes[ln].figureType != -1)
            haloVoidR[li] = max(evalSDF(vec3(0.0), u_nodes[ln].figureType, lps), 0.0);
    }
}

void resetHaloSlots()
{
    for (int s = 0; s < GLOW_TRACK_SLOTS; s++) slotNode[s] = -1;
}

bool haloSlotTracks(int nodeId)
{
    for (int s = 0; s < GLOW_TRACK_SLOTS; s++)
        if (slotNode[s] == nodeId) return true;
    return false;
}

struct HaloStep
{
    vec3 glow;   // вклад слотов, финализированных на этом шаге
    bool nearHalo;   // луч в гейт-оболочке хотя бы одного источника
    bool hasSlots; // есть занятые слоты
};

// Шаг трекинга: обновление перигеев, входы в зоны, выходы из зон
HaloStep updateHaloTracking(vec3 point, float t, float glowAperture)
{
    HaloStep res;
    res.glow = vec3(0.0);
    res.nearHalo = false;

    for (int s = 0; s < GLOW_TRACK_SLOTS; s++) slotSeen[s] = false;

    for (int li = 0; li < LIGHT_MAX_COUNT; li++)
    {
        if (li >= haloCount) break;
        if (haloNode[li] < 0) continue;

        float glowRadiusLi = t * glowAperture * (0.5 + haloBound[li]);
        // дешёвый гейт по описанной сфере: консервативен, зону не пропускает
        float approxDist = length(point - haloPos[li]) - haloBound[li];
        if (approxDist > glowRadiusLi) continue;
        res.nearHalo = true;

        // точная дистанция до самого источника (с его же squash-поправкой)
        float dL = evalNodeSDF(point, haloNode[li], haloParams[li]).x;
        if (dL >= glowRadiusLi * haloMinSq[li]) continue; // вне зоны

        bool found = false;
        for (int s = 0; s < GLOW_TRACK_SLOTS; s++)
        {
            if (slotNode[s] == haloNode[li]) // уже отслеживается
            {
                slotSeen[s] = true;
                found = true;
                if (dL < slotMinDist[s])
                {
                    slotMinDist[s] = dL;
                    slotMinRadius[s] = glowRadiusLi;
                }
                break;
            }
        }
        if (!found) // новый вход: занять свободный слот
        {
            for (int s = 0; s < GLOW_TRACK_SLOTS; s++)
            {
                if (slotNode[s] < 0)
                {
                    slotNode[s] = haloNode[li];
                    slotMinDist[s] = dL;
                    slotMinRadius[s] = glowRadiusLi;
                    slotSeen[s] = true;
                    break;
                }
            }
        }
    }

    // финализация слотов, чьи источники покинули зону: перигей зафиксирован
    res.hasSlots = false;
    for (int s = 0; s < GLOW_TRACK_SLOTS; s++)
    {
        if (slotNode[s] < 0) continue;
        if (!slotSeen[s])
        {
            res.glow += grazingGlowColor(slotNode[s], slotMinDist[s], slotMinRadius[s]);
            slotNode[s] = -1;
        }
        else res.hasSlots = true;
    }
    return res;
}

// Финализация всех активных слотов после цикла марча
// (хит в источник, предел шагов или дальности)
vec3 finalizeHaloSlots()
{
    vec3 glow = vec3(0.0);
    for (int s = 0; s < GLOW_TRACK_SLOTS; s++)
    {
        if (slotNode[s] >= 0)
        {
            glow += grazingGlowColor(slotNode[s], slotMinDist[s], slotMinRadius[s]);
            slotNode[s] = -1;
        }
    }
    return glow;
}

// Сетка с антиалиасингом: линия никогда не тоньше ~1.5 пикселя —
// вместо утоньшения она плавно растворяется. Нет муара ни вблизи, ни вдали.
float gridLine(float p, float width)
{
    float w = fwidth(p) + 1e-7;                    // ширина пикселя в единицах p
    float d = min(fract(p), 1.0 - fract(p));       // расстояние до ближайшей линии
    float lw = min(max(width, w * 1.5), 0.5);      // зажать: не тоньше пикселя
    float line = 1.0 - smoothstep(lw - w, lw + w, d);
    float fade = clamp(width / (w * 1.5), 0.0, 1.0); // зажатая линия гаснет
    return line * fade;
}

// Единое направление на светило (азимут/высота).
vec3 sunDirection()
{
    float phi   = radians(u_skySunAzimuthDeg);   // от +Z к +X
    float theta = radians(u_skySunElevationDeg); // над горизонтом
    float cth = cos(theta);
    return normalize(vec3(
        cth * sin(phi),
        sin(theta),
        cth * cos(phi)
    ));
}

// Спектрально упрощённая модель неба: Рэлеевское затухание к горизонту,
// сияние вокруг диска и сам диск. Цвет атмосферы — u_ambientLightColor,
// яркость диска — u_ambientLightIntensity.
vec3 skyRadiance(vec3 dir, bool includeSunDisc)
{
    vec3 sunDir = sunDirection();
    float cosSun = dot(dir, sunDir);
    vec3 atmColor = u_ambientLightColor; // перепрофилирован в цвет атмосферы

    // Оптическая толщина воздуха: минимум у зенита, растёт к горизонту.
    float mu = clamp(dir.y, 0.02, 1.0);
    float opt = u_skyAirDensity / mu;

    vec3 zenith  = atmColor * u_ambientLightIntensity;
    vec3 horizon = (atmColor * exp(-opt * 0.15) + vec3(0.03, 0.015, 0.005))
                 * u_ambientLightIntensity;
    vec3 sky = mix(horizon, zenith, pow(mu, 0.5));

    // Общий скейл яркости светила (аналогичный lightPowerEstimate).
    float sunScale = 3.0 * u_LightPowerScale;

    // Сияние вокруг диска: два лепестка — узкий яркий обод и широкая слабая аура.
    float innerGlow = pow(max(cosSun, 0.0), 1.0 / max(SKY_SUN_GLOW_INNER, 0.001));
    float outerGlow = pow(max(cosSun, 0.0), 1.0 / max(SKY_SUN_GLOW, 0.001));
    float glow = innerGlow * 0.6 + outerGlow * 0.1;
    sky += atmColor * glow * u_ambientLightIntensity * sunScale;

    if (includeSunDisc)
    {
        float cosRadius = cos(u_skySunAngRadius);
        float disc = smoothstep(cosRadius, cosRadius + 0.0005, cosSun);
        sky += atmColor * disc * SKY_SUN_DISC_EXPOSURE * u_ambientLightIntensity * sunScale;
    }

    // Ниже горизонта: быстрое затухание к отражённому от земли свету,
    // чтобы вертикальные поверхности не получали чёрную полосу терминатора.
    if (dir.y < 0.0)
    {
        float under = exp(dir.y * 4.0);
        vec3 groundBounce = atmColor * u_ambientLightIntensity * SKY_GROUND_BOUNCE;
        sky = mix(groundBounce, sky, under);
    }

    return sky;
}

// Пропускание атмосферы на заданной дистанции.
float aerialTransmittance(float dist)
{
    return exp(-SKY_HAZE_DENSITY * dist);
}

// Однократное рассеяние + фаза Хеньи-Гринштейна в направлении светила.
vec3 applyAerialPerspective(vec3 color, float dist, vec3 dir)
{
    float T = aerialTransmittance(dist);
    vec3 sunDir = sunDirection();
    float cosGamma = dot(normalize(dir), sunDir);
    float g = SKY_HG_G;
    float denom = 1.0 + g * g - 2.0 * g * cosGamma;
    float phase = (1.0 - g * g) / (4.0 * PI * pow(max(denom, 0.001), 1.5));
    vec3 inscatter = u_ambientLightColor
                   * u_ambientLightIntensity
                   * (1.0 - T)
                   * phase;
    return color * T + inscatter;
}

// Направления лучей неба. Светило — 1 или 3 луча (треугольник внутри диска),
// остальные — косинус-взвешенная полусфера вокруг N по спирали Фибоначчи.
// Возвращает общее число лучей и количество солнечных сэмплов в начале массива.
int skyRayDirections(vec3 N, int numRays, out vec3 dirs[SKY_RAY_MAX], out int sunCount)
{
    numRays = clamp(numRays, 1, SKY_RAY_MAX);

    vec3 sunDir = sunDirection();
    // Светило считается видимым поверхности, если его диск не целиком за горизонтом.
    bool sunVisible = dot(sunDir, N) > -sin(u_skySunAngRadius + SKY_SUN_GLOW);

    int idx = 0;
    sunCount = 0;

    if (sunVisible && numRays >= SKY_SUN_MULTI_MIN_RAYS)
    {
        // Треугольная выборка диска светила (равносторонний треугольник).
        vec3 axisU, axisV;
        buildRayBasis(sunDir, axisU, axisV);
        float rho = SKY_SUN_TRIANGLE_RADIUS * u_skySunAngRadius;
        float phaseShift = (u_sampleCount > 1) ? u_ngonPhase : 0.0;
        for (int k = 0; k < 3; k++)
        {
            float phi = phaseShift + float(k) * 2.0943951; // 2π/3
            dirs[idx] = normalize(sunDir + (axisU * cos(phi) + axisV * sin(phi)) * rho);
            idx++;
        }
        sunCount = 3;
    }
    else if (sunVisible)
    {
        dirs[idx] = sunDir;
        idx++;
        sunCount = 1;
    }

    if (numRays <= idx)
        return idx;

    vec3 axisU, axisV;
    buildRayBasis(N, axisU, axisV);

    int ring = numRays - idx;
    for (int k = 0; k < SKY_RAY_MAX; k++)
    {
        if (k >= ring)
            break;

        float t = (float(k) + 0.5) / float(ring); // 0..1
        float z = sqrt(1.0 - t);                  // cos(theta), Lambert-спираль
        float r = sqrt(max(1.0 - z * z, 0.0));
        float phaseShift = (u_sampleCount > 1) ? u_ngonPhase : 0.0;
        float phi = float(k) * 2.399963 + phaseShift; // золотой угол + фаза только при накоплении

        dirs[idx + k] = normalize(
            axisU * cos(phi) * r +
            axisV * sin(phi) * r +
            N     * z
        );
    }

    return numRays;
}

vec3 getBackground(vec3 origin, vec3 dir, out float backgroundAlpha, bool directRay)
{
    vec3 backgroundColor = skyRadiance(dir, true);
    backgroundAlpha = 1.0;
    float floorAlpha = 0.0;

    // --- Пол ---
    if (u_showFloor == 1 && dir.y < -0.001)
    {
        float t_floor = (-1.0 - origin.y) / dir.y;
        if (t_floor > 0.0)
        {
            vec2 floorPos = origin.xz + dir.xz * t_floor;

            // Две сетки: метровая и крупная десятиметровая —
            // крупная продолжает читаться там, где мелкая уже растворилась
            float g1  = max(gridLine(floorPos.x,        0.02/4.0),
                            gridLine(floorPos.y,        0.02/4.0));
            float g10 = max(gridLine(floorPos.x / 10.0, 0.012/4.0),
                            gridLine(floorPos.y / 10.0, 0.012/4.0));

            vec3 lineColor = vec3(0.03, 0.79, 0.0);
            vec3 floorColor = vec3(0.15);
            floorColor = mix(floorColor, lineColor * 0.6, g1);
            floorColor = mix(floorColor, lineColor,       g10);

            // Оси в цветах меток из UI: X — красная, Z — синяя
            float w = fwidth(floorPos.x) + 1e-7;
            float axisZ = 1.0 - smoothstep(0.03 - w, 0.03 + w, abs(floorPos.x));
            float axisX = 1.0 - smoothstep(0.03 - w, 0.03 + w, abs(floorPos.y));
            floorColor = mix(floorColor, vec3(0.38, 0.69, 0.94), axisZ);
            floorColor = mix(floorColor, vec3(0.88, 0.42, 0.46), axisX);

            // Воздушная перспектива на полу: дальние участки уходят в небо,
            // ближние читаются. floorAlpha — пропускание: чем дальше,
            // тем больше неба в пикселе.
            floorAlpha = aerialTransmittance(t_floor);
            floorColor = applyAerialPerspective(floorColor, t_floor, dir);
            backgroundColor = mix(backgroundColor, floorColor, floorAlpha);
        }
    }

    if (u_transparentBackground == 1 && directRay)
    {
        backgroundAlpha = 0.0;
        // Полностью прозрачный фон (не исключает преломление света и фоновое освещение)
    }

    return backgroundColor;
}

// Функция для получения нормали к поверхности с учетом текстурной деформации
vec3 calcNormalGlobal(vec3 p)
{
    vec2 e = vec2(1.0, -1.0) * 0.0001;
    return normalize(
                    e.xyy*mapBase(p+e.xyy).x 
                                + 
                    e.yyx*mapBase(p+e.yyx).x
                                +
                    e.yxy*mapBase(p+e.yxy).x
                                +
                    e.xxx*mapBase(p+e.xxx).x
                    );
}

// Нормаль с задаваемым эпсилоном градиента
vec3 calcNormalGlobalEps(vec3 p, float eps)
{
    vec2 e = vec2(1.0, -1.0) * eps;
    return normalize(
        e.xyy * mapBase(p + e.xyy).x +
        e.yyx * mapBase(p + e.yyx).x +
        e.yxy * mapBase(p + e.yxy).x +
        e.xxx * mapBase(p + e.xxx).x);
}

// Ambient Occlusion по нормали поверхности.
// mapBase намеренно используется вместо mapShadow:
// геометрия, включая стекло, визуально добавляет локальное AO.
float calcAO(vec3 p, vec3 n)
{
    if (u_fastMode) return 1.0;

    const int AO_SAMPLES = 5;

    float occlusion = 0.0;
    float weight = 1.0;

    for (int i = 0; i < AO_SAMPLES; i++)
    {
        float t = float(i) / float(AO_SAMPLES - 1);
        float sampleDist = mix(0.015, 0.35, t * t);

        float sceneDist = mapBase(p + n * sampleDist).x;
        float contribution = max(sampleDist - sceneDist, 0.0);

        occlusion += contribution * weight;
        weight *= 0.72;
    }

    return clamp(1.0 - 2.5 * occlusion, 0.0, 1.0);
}

// Функция Френеля по Шлику - аппроксимация отражательной способности материала в зависимости от угла падения света.
vec3 fresnelSchlick(float cosTheta, vec3 F0)
{
    return F0 + (1.0 - F0) * pow(1.0 - cosTheta, 5.0);
}

// Функция распределения микрофасетов GGX
// roughness управляет шириной и формой блика
float D_GGX(float NdotH, float roughness)
{
    float a = max(roughness * roughness, 0.02);
    float a2 = a * a;
    float d = NdotH * NdotH * (a2 - 1.0) + 1.0;
    return a2 / max(PI * d * d, 0.001);
}

// Геометрическая функция Шлика-ГГХ
// Отвечает за маскирование и самозатенение микрофасетов
float G_SchlickGGX(float NdotX, float roughness)
{
    float r = roughness + 1.0;
    float k = (r * r) * 0.125;
    return NdotX / max(NdotX * (1.0 - k) + k, 0.001);
}

// Геометрическая функция Смита
// Комбинирует видимость для взгляда и света
float G_Smith(float NdotV, float NdotL, float roughness)
{
    return G_SchlickGGX(NdotV, roughness) * G_SchlickGGX(NdotL, roughness);
}

// Может ли луч ещё долететь до цели: пересечение с её описанной сферой.
// Дёшево (без марча); сфера консервативна — ложных отсечений не даёт
bool rayHitsSphere(vec3 ro, vec3 rd, vec3 center, float radius)
{
    vec3 oc = center - ro;
    float tca = dot(oc, rd);
    if (tca < 0.0) return false; // сфера позади луча
    float d2 = dot(oc, oc) - tca * tca;
    return d2 <= radius * radius;
}

bool dispersionToggle = false;
int dispersionChannel = -1;

// Сегментный марш конуса от старта до источника с преломлениями.
vec3 lightVisibilityCone(vec3 ro, vec3 target, float aperture, int maxRefract,
out bool outRefracted, out int outGlassNode,
float budgetScale, vec3 goalCenter, float goalRadius,
bool ignoreGlass,
int selfNode,        // узел, которому принадлежит шейдируемая точка; -1 — родителя нет (старое поведение)
out float outOwnLen  // суммарный путь луча внутри selfNode; 0 — не пересекал
)
{
    outRefracted = false;
    outGlassNode = -1;
    outOwnLen = 0.0; // урок части II: out-параметр инициализируется в шапке;
                     // дальше накопление идёт ПРЯМО в него — после инициализации
                     // чтение определено, return'ы трогать не нужно
    vec3 tint = vec3(1.0);

    if (!ignoreGlass && u_outputLinear && u_dispersion > 0 && u_sampleCount > 1)
    {
        dispersionToggle = true;
        dispersionChannel = u_sampleId % 3;
    }

    if (dispersionToggle)
    {
        if(dispersionChannel == 0)
            tint = vec3(3.0, 0.0, 0.0);
        else if(dispersionChannel == 1)
            tint = vec3(0.0, 3.0, 0.0);
        else if(dispersionChannel == 2)
            tint = vec3(0.0, 0.0, 3.0);
    }
    
    
    vec3 from = ro;
    float remain = length(target - ro);
    vec3 dir = (target - ro) / max(remain, 1e-5);


    // Бюджет марша с запасом: преломлённый луч идёт по физике,
    // и фокус линзы может лежать дальше исходной цели.
    // Потолок — разумная дальность сцены, как в первичном марче
    float budget = min(remain * budgetScale + 1.0, 30.0);
    float coneBase = 0.0; // пройденная дистанция от ro: конус растёт сквозь сегменты
    int refractNode = -1; // узел, внутрь которого луч вошёл преломлением
    float curIor = 1.0; // показатель среды, в которой летит луч

    float innerLen = 0.0; // путь в текущем теле: тинт на выходе и канал пути
    
    for (int seg = 0; seg <= maxRefract; seg++)
    {
        bool arrived = false, blocked = false, refracted = false;
        float t = 0.02;
        float prevDist = 1e20;
        bool isGrazing = false;
        bool grazingIsLight = false; // скольжение идёт по краю источника
        bool grazingIsSelf = false;
        float minGrazDist = 1e20;
        float grazConeR = 1e-4;

        // Луч в воздухе с исчерпанным лимитом преломлений больше не согнётся:
        // если он не пересекает описанную сферу цели, дальнейший марш бессмысленен.
        // Промежуточные сегменты не проверяем: до выхода из последнего стекла
        // луч ещё может повернуть к цели преломлением (край линзы после входа
        // легко летит мимо сферы маленького источника, а выход догибает на него)
        if (seg == maxRefract && refractNode < 0 &&
            !rayHitsSphere(from, dir, goalCenter, goalRadius))
        {
            gVisFail = 1;
            return vec3(0.0);
        }

        // Тинт погашен поглощением стёкол — дальше нечего нести.
        // Тинт монотонно убывает, проверка безопасна на любом сегменте
        if (dot(tint, vec3(1.0)) < 0.009)
        {
            gVisFail = 2;
            return vec3(0.0);
        }

        for (int i = 0; i < 24; i++)
        {
            vec3 p = from + dir * t;
            vec2 mr = mapBase(p);
            float d = mr.x;

            float coneR = max((coneBase + t) * aperture, 0.001);

            // Луч внутри тела, в которое вошёл преломлением: отрицательная
            // дистанция — не пересечение, а запас до выхода из тела
            if (d < 0.0 && int(mr.y) == refractNode)
            {
                prevDist = d;
                float dt = max(-d, (refractNode == selfNode) ? SSS_SELF_PASS_MIN_STEP : 0.005);
                t += dt;
                if (refractNode >= 0 && !ignoreGlass) innerLen += dt;

                if (coneBase + t > budget) break;
                continue;
            }

            // --- твёрдое пересечение ---
            if (d < max(0.001, coneR * 0.1))
            {
                noteReach(p);
                int nid = int(mr.y);
                if (nid < 0)
                {
                    // CSG-пустота / артефакт SDF: id = -1 означает отсутствие
                    // реальной поверхности, поэтому блокировать нечего — прыгаем
                    // сквозь, как при self-грейсе.
                    t += max(0.03, coneR * 0.5);
                    prevDist = d;
                    continue;
                }
                int nps = int(u_nodes[nid].params.x);
                vec3 hAlb; int hMid;
                getNodeAlbedoAndMaterialId(nps, hAlb, hMid);
                Material hMat = u_materialsList[hMid];

                if (hMat.emission > 0.0) { arrived = true; break; } // долетели до света

                // Родитель нейтрален: без преломления и без тинта.
                // Участок «тело → точка → камера» считает main,
                // свою толщу — аналитический SSS. Марш тело проходит
                // насквозь: нужны точка выхода и долёт до источника
                // (чужие окклюдеры за телом гасят просвет)
                // Нейтрален только родитель-СРЕДА: непрозрачное своё тело —
                // честный блокер, его хит обязан падать в blocked (самотень)
                if (nid == selfNode && (hMat.transmission > 0.001 || hMat.subsurface > 0.001))
                {
                    if (refractNode != selfNode)
                    {
                        refractNode = nid;
                        // Входящий луч сжигает отложенное самопокрытие;
                        // уходящий вдоль поверхности — остаётся
                        if (isGrazing && grazingIsSelf) isGrazing = false;
                    }
                    else
                    {
                        // Выход из своего тела: фиксируем накопленную хорду для SSS
                        outOwnLen = innerLen;
                        innerLen = 0.0;
                        refractNode = -1;
                    }
                    t += max(0.03, coneR * 0.5);
                    prevDist = d;
                    continue;
                }

                if (hMat.transmission > 0.001)
                {
                    if (ignoreGlass)
                    {
                        // вторичные лучи: стекло не тинтует и не гнет луч —
                        // запоминаем узел, марш внутри тела сам выведет наружу
                        refractNode = (nid == refractNode) ? -1 : nid;
                        t += max(0.03, coneR * 0.5);
                        prevDist = d;
                        continue;
                    }

                    if (seg < maxRefract)
                    {
                        // стекло: преломление; окраска — на выходе из тела
                        // по длине пути в нём, не на границе.
                        // Попадание в источник решает только верификация хитом.
                        // Вход: eta = среда/тело; выход из того же тела — в сторону воздуха
                        vec3 n = calcNormalGlobal(p);
                        if (dot(n, dir) > 0.0) n = -n;
                        bool exiting = (nid == refractNode);

                        float disp = float(dispersionChannel - 1) * u_dispersion * DISPERSION_COEFF;
                        float nextIor = max(hMat.ior * (1.0 + (dispersionToggle? disp : 0.0) ), 1.01);

                        float eta = exiting ? curIor : curIor / nextIor;
                        vec3 rdir = refract(dir, n, eta);
                        if (dot(rdir, rdir) < 0.5) rdir = dir; // TIR — идём прямо

                        if (exiting)
                        {
                            // Поглощение по пути: стеклянное плюс объёмное, если тело с SSS
                            vec3 sigmaG = -log(max(hAlb, vec3(1e-3))) * clamp(hMat.transmission, 0.0, 1.0);
                            if (hMat.subsurface > 0.001)
                                sigmaG += -log(max(hMat.sssColor, vec3(1e-3))) / max(hMat.subsurface, 1e-3);
                            tint *= exp(-sigmaG * innerLen);
                            innerLen = 0.0;
                        }
                        else
                        {
                            innerLen = 0.0; // вход: путь в теле пошёл с нуля
                        }

                        coneBase += t; // конус продолжает расти от исходного ro
                        from = p + rdir * 0.02;
                        dir = rdir;
                        refractNode = exiting ? -1 : nid;
                        curIor = exiting ? 1.0 : nextIor;
                        if (outGlassNode < 0) outGlassNode = nid; // линза — первое стекло на пути

                        outRefracted = true;
                        refracted = true;
                        break;
                    }
                }

                // SSS-тело: проход без преломления и тинта, меряется путь внутри.
                // Тинт телу не положен: поглощение считает SSS-модель на шейдинге
                if (hMat.subsurface > 0.001 && seg < maxRefract)
                {
                    bool exiting = (nid == refractNode);
                    if (exiting)
                    {
                        // Поглощение SSS-тела по длине пути: тонкая стена протекает
                        // оттенённым светом, толстая затухает сама — обрыв не нужен
                        vec3 sigS = -log(max(hMat.sssColor, vec3(1e-3))) / max(hMat.subsurface, 1e-3);
                        tint *= exp(-sigS * innerLen);
                        innerLen = 0.0;
                    }
                    else
                    {
                        innerLen = 0.0;
                    }
                    coneBase += t;
                    from = p + dir * 0.02;
                    refractNode = exiting ? -1 : nid;
                    refracted = true; // марш продолжается; outRefracted не ставим
                    break;
                }

                // Ложный приповерхностный хит непрозрачного self:
                // SDF занижен у сжатых фигур/рельефа/CSG-швов, а bias всего 0.006–0.03.
                // Пропускаем хит на малых дистанциях, сохраняя самотень дальше.
                if (nid == selfNode && coneBase + t < SELF_HIT_GRACE_DIST)
                {
                    t += max(0.03, coneR * 0.5);
                    prevDist = d;
                    continue;
                }

                // Аварийная грейс-зона для любого блокера (выключена по умолчанию).
                // Включается, если декодирование покажет, что лучи умирают на
                // CSG-партнёре прямо у поверхности.
                if (ALL_HIT_GRACE_DIST > 0.0 && coneBase + t < ALL_HIT_GRACE_DIST && nid != selfNode)
                {
                    t += max(0.03, coneR * 0.5);
                    prevDist = d;
                    continue;
                }
                
                gVisBlocker = nid;
                gVisBlockT = coneBase + t;
                blocked = true; // непрозрачное или бюджет преломлений исчерпан
                break;
            }

            // --- мягкое покрытие: скольжение конуса мимо непрозрачного ---
            if (d < coneR)
            {
                if (!isGrazing)
                {
                    int nid = int(mr.y);
                    // selfNode исключаем: конус, рождённый на поверхности,
                    // всегда «скользит» по этой же поверхности — это артефакт рождения,
                    // а не реальное затенение. Самотень обрабатывается hard-hit.
                    if (nid >= 0 && nid != selfNode)
                    {
                        int nps = int(u_nodes[nid].params.x);
                        Material gMat = u_materialsList[NODE_MATERIAL_ID(nps)];
                        // стекло пенумбры не даёт: оно преломляет.
                        // Источник наоборот трекается: скольжение конуса по его краю —
                        // частичное попадание, а не промах
                        if (gMat.transmission <= 0.001)
                        {
                            isGrazing = true;
                            grazingIsLight = gMat.emission > 0.0;
                            grazingIsSelf = false; // self исключён выше
                            minGrazDist = d;
                            grazConeR = coneR;
                        }
                    }
                }
                else if (d < minGrazDist)
                {
                    minGrazDist = d;
                    grazConeR = coneR; // радиус конуса в перигее
                }
            }

            // выход из скольжения: перигей пройден — фиксируем покрытие
            if (isGrazing && d > prevDist && d > grazConeR)
            {
                float coverage = clamp(1.0 - minGrazDist / max(grazConeR, 1e-4), 0.0, 1.0);
                // скольжение по краю источника: доля конуса попала — частичное прибытие
                if (grazingIsLight) return tint * coverage;
                tint *= 1.0 - coverage;
                isGrazing = false;
                grazingIsSelf = false;
            }

            prevDist = d;
            float dt = isGrazing ? max(d * 0.5, 0.005) : max(d, coneR * 0.5);
            t += dt;
            if (refractNode >= 0 && !ignoreGlass) innerLen += dt;
            if (seg == 0 && refractNode < 0 && t > remain + 0.05) break; // прямой сегмент — цель позади
            if (coneBase + t > budget) break; // бюджет исчерпан, ничего не встретили
        }

        // марш кончился в зоне скольжения (хит в источник у края окклюдера
        // и т.п.) — фиксируем накопленное покрытие
        if (isGrazing)
        {
            float coverage = clamp(1.0 - minGrazDist / max(grazConeR, 1e-4), 0.0, 1.0);
            // скольжение по краю источника: доля конуса попала — частичное прибытие
            if (grazingIsLight) return tint * coverage;
            tint *= 1.0 - coverage;
        }

        if (arrived) return tint;
        if (blocked) { gVisFail = 3; return vec3(0.0); }
        // Перенаправление — не поглощение: луч прошёл сквозь стекло,
        // вклад несёт его фильтр
        if (!refracted) { gVisFail = 4; return outRefracted ? tint : vec3(0.0); }

    }
    
    { gVisFail = 5; return outRefracted ? tint : vec3(0.0); }
}

// Видимость неба.
// fullMarch == false: дешёвый марш по mapShadow (кольцевые лучи неба).
// fullMarch == true : полный аналог lightVisibilityCone (только для луча к светилу):
//                     преломления, тинт стёкол, SSS-проход, self-грейс.
// Прибытие — чистый выход за дистанцию dist; возвращает тинт (vec3).
vec3 skyVisibilityCone(vec3 ro, vec3 dir, float dist, float aperture,
                       bool fullMarch, int selfNode, out float outOwnLen)
{
    outOwnLen = 0.0;

    if (!fullMarch)
    {
        // --- Дешёвый кольцевой марш: только непрозрачное, стекло/источники игнорируются ---
        float vis = 1.0;
        bool grazing = false;
        float minGrazDist = 1e20;
        float grazConeR = 1e-4;

        float t = 0.02;
        float prevDist = 1e20;
        for (int i = 0; i < 24; i++)
        {
            vec3 p = ro + dir * t;
            vec2 mr = mapShadow(p);
            float d = mr.x;
            int nid = int(mr.y);
            float coneR = max(t * aperture, 0.001);

            // self-грейс, как у физических источников
            if (nid == selfNode && t < SELF_HIT_GRACE_DIST)
            {
                t += max(0.03, coneR * 0.5);
                prevDist = d;
                continue;
            }

            if (d < max(0.001, coneR * 0.1))
            {
                gVisFail = 3;
                return vec3(0.0);
            }

            if (d < coneR)
            {
                if (!grazing || d < minGrazDist)
                {
                    minGrazDist = d;
                    grazConeR = coneR;
                }
                grazing = true;
            }
            else if (grazing && d > prevDist)
            {
                float coverage = smoothstep(0.0, 1.0, clamp(1.0 - minGrazDist / max(grazConeR, 1e-4), 0.0, 1.0));
                vis *= 1.0 - coverage;
                grazing = false;
            }

            prevDist = d;
            t += grazing ? max(d * 0.5, 0.005) : max(d, coneR * 0.5);
            if (t > dist) break;
        }

        if (t <= dist) { gVisFail = 4; return vec3(0.0); }

        if (grazing)
            vis *= 1.0 - smoothstep(0.0, 1.0, clamp(1.0 - minGrazDist / max(grazConeR, 1e-4), 0.0, 1.0));

        // Художественное смягчение небесных теней (кольцевых/дешёвых лучей).
        vis = mix(1.0, vis, SKY_SHADOW_SOFTNESS);

        return vec3(vis);
    }

    // --- Полный марш для луча к светилу (адаптировано из lightVisibilityCone) ---
    vec3 tint = vec3(1.0);

    if (u_outputLinear && u_dispersion > 0 && u_sampleCount > 1)
    {
        dispersionToggle = true;
        dispersionChannel = u_sampleId % 3;
    }

    if (dispersionToggle)
    {
        if(dispersionChannel == 0)
            tint = vec3(3.0, 0.0, 0.0);
        else if(dispersionChannel == 1)
            tint = vec3(0.0, 3.0, 0.0);
        else if(dispersionChannel == 2)
            tint = vec3(0.0, 0.0, 3.0);
    }

    vec3 from = ro;
    vec3 curDir = dir;
    float budget = min(dist * 2.0 + 1.0, 30.0);
    float coneBase = 0.0;
    int refractNode = -1;
    float curIor = 1.0;
    float innerLen = 0.0;

    for (int seg = 0; seg <= CONE_MAX_REFRACT_CHEAP; seg++)
    {
        bool blocked = false;
        bool refracted = false;
        float t = 0.02;
        float prevDist = 1e20;
        bool isGrazing = false;
        float minGrazDist = 1e20;
        float grazConeR = 1e-4;

        for (int i = 0; i < 24; i++)
        {
            vec3 p = from + curDir * t;
            vec2 mr = mapBase(p);
            float d = mr.x;
            int nid = int(mr.y);
            float coneR = max((coneBase + t) * aperture, 0.001);

            // Луч внутри тела, в которое вошёл преломлением
            if (d < 0.0 && nid == refractNode)
            {
                prevDist = d;
                float dt = max(-d, (refractNode == selfNode) ? SSS_SELF_PASS_MIN_STEP : 0.005);
                t += dt;
                if (refractNode >= 0) innerLen += dt;
                if (coneBase + t > budget) break;
                continue;
            }

            // Твёрдое пересечение
            if (d < max(0.001, coneR * 0.1))
            {
                noteReach(p);
                if (nid < 0)
                {
                    t += max(0.03, coneR * 0.5);
                    prevDist = d;
                    continue;
                }
                int nps = int(u_nodes[nid].params.x);
                vec3 hAlb; int hMid;
                getNodeAlbedoAndMaterialId(nps, hAlb, hMid);
                Material hMat = u_materialsList[hMid];

                // Своё прозрачное/SSS-тело: проходим насквозь, замеряем хорду
                if (nid == selfNode && (hMat.transmission > 0.001 || hMat.subsurface > 0.001))
                {
                    if (refractNode != selfNode)
                    {
                        refractNode = nid;
                        if (isGrazing) isGrazing = false;
                    }
                    else
                    {
                        outOwnLen = innerLen;
                        innerLen = 0.0;
                        refractNode = -1;
                    }
                    t += max(0.03, coneR * 0.5);
                    prevDist = d;
                    continue;
                }

                // Стекло: преломление и тинт на выходе
                if (hMat.transmission > 0.001 && seg < CONE_MAX_REFRACT_CHEAP)
                {
                    vec3 n = calcNormalGlobal(p);
                    if (dot(n, curDir) > 0.0) n = -n;
                    bool exiting = (nid == refractNode);
                    float disp = float(dispersionChannel - 1) * u_dispersion * DISPERSION_COEFF;
                    float nextIor = max(hMat.ior * (1.0 + (dispersionToggle? disp : 0.0) ), 1.01);
                    float eta = exiting ? curIor : curIor / nextIor;
                    vec3 rdir = refract(curDir, n, eta);
                    if (dot(rdir, rdir) < 0.5) rdir = curDir;

                    if (exiting)
                    {
                        vec3 sigmaG = -log(max(hAlb, vec3(1e-3))) * clamp(hMat.transmission, 0.0, 1.0);
                        if (hMat.subsurface > 0.001)
                            sigmaG += -log(max(hMat.sssColor, vec3(1e-3))) / max(hMat.subsurface, 1e-3);
                        tint *= exp(-sigmaG * innerLen);
                        innerLen = 0.0;
                    }
                    else
                    {
                        innerLen = 0.0;
                    }

                    coneBase += t;
                    from = p + rdir * 0.02;
                    curDir = rdir;
                    refractNode = exiting ? -1 : nid;
                    curIor = exiting ? 1.0 : nextIor;
                    refracted = true;
                    break;
                }

                // SSS-тело чужое/своё без преломления: проход + поглощение
                if (hMat.subsurface > 0.001 && seg < CONE_MAX_REFRACT_CHEAP)
                {
                    bool exiting = (nid == refractNode);
                    if (exiting)
                    {
                        vec3 sigS = -log(max(hMat.sssColor, vec3(1e-3))) / max(hMat.subsurface, 1e-3);
                        tint *= exp(-sigS * innerLen);
                        innerLen = 0.0;
                    }
                    else
                    {
                        innerLen = 0.0;
                    }
                    coneBase += t;
                    from = p + curDir * 0.02;
                    refractNode = exiting ? -1 : nid;
                    refracted = true;
                    break;
                }

                // Ложный приповерхностный хит непрозрачного self
                if (nid == selfNode && coneBase + t < SELF_HIT_GRACE_DIST)
                {
                    t += max(0.03, coneR * 0.5);
                    prevDist = d;
                    continue;
                }

                if (ALL_HIT_GRACE_DIST > 0.0 && coneBase + t < ALL_HIT_GRACE_DIST && nid != selfNode)
                {
                    t += max(0.03, coneR * 0.5);
                    prevDist = d;
                    continue;
                }

                gVisBlocker = nid;
                gVisBlockT = coneBase + t;
                blocked = true;
                break;
            }

            // Мягкое скольжение по краю окклюдера
            if (d < coneR)
            {
                if (!isGrazing)
                {
                    int gid = int(mr.y);
                    if (gid >= 0 && gid != selfNode)
                    {
                        int gps = int(u_nodes[gid].params.x);
                        Material gMat = u_materialsList[NODE_MATERIAL_ID(gps)];
                        if (gMat.transmission <= 0.001)
                        {
                            isGrazing = true;
                            minGrazDist = d;
                            grazConeR = coneR;
                        }
                    }
                }
                else if (d < minGrazDist)
                {
                    minGrazDist = d;
                    grazConeR = coneR;
                }
            }

            // Выход из зоны скольжения
            if (isGrazing && d > prevDist && d > grazConeR)
            {
                float coverage = clamp(1.0 - minGrazDist / max(grazConeR, 1e-4), 0.0, 1.0);
                tint *= 1.0 - coverage;
                isGrazing = false;
            }

            prevDist = d;
            float dt = isGrazing ? max(d * 0.5, 0.005) : max(d, coneR * 0.5);
            t += dt;
            if (refractNode >= 0) innerLen += dt;
            if (coneBase + t > budget) break;
        }

        if (isGrazing)
        {
            float coverage = clamp(1.0 - minGrazDist / max(grazConeR, 1e-4), 0.0, 1.0);
            tint *= 1.0 - coverage;
        }

        if (blocked) { gVisFail = 3; return vec3(0.0); }

        // Чистый выход к небу внутри бюджета = прибытие
        if (!refracted)
            return tint;
    }

    { gVisFail = 5; return vec3(0.0); }
}


// ---------------------------------------------------------------------
// Точки сэмплирования источника для площадного блика и тени.
// Фигура считается вписанной в единичный куб: полуоси — вытягивание
// покомпонентно, проекция на плоскость выборки — функция опоры бокса
// dot(abs(axis), semi). Плоскость выборки проходит через центр
// источника перпендикулярно лучу — вершины n-угольника лежат
// внутри силуэта при любом ракурсе.
// Полая фигура (voidR > 0): центральный луч проходит сквозь пустоту,
// он пропускается, а вершины ставятся на середину между радиусом
// пустоты и кромкой силуэта — на осевую линию кольца.
// Первая вершина совмещена с проекцией самой длинной оси фигуры:
// вытянутый силуэт покрывается по длине при любом числе лучей.
// Возвращает число точек; pts — мировые координаты.
// ---------------------------------------------------------------------
int lightSamplePoints(int lightNodeId, vec3 from, int extraRays, float voidR, out vec3 pts[LIGHT_EXTRA_RAY_MAX])
{
    int paramsStart = int(u_nodes[lightNodeId].params.x);
    vec3 center = NODE_TRANSLATION(paramsStart);
    vec3 rotation = NODE_ROTATION(paramsStart);
    vec3 elong;
    float scale;
    getNodeElongationAndScale(paramsStart, elong, scale);
    vec3 semi = max(elong, vec3(0.001)) * abs(scale);

    vec3 dirC = normalize(center - from);
    vec3 axisU, axisV;
    buildRayBasis(dirC, axisU, axisV);

    // Полуразмеры проекции бокса на плоскость выборки (функция опоры)
    float halfW = dot(abs(inverseRotate3D(axisU, rotation)), semi);
    float halfH = dot(abs(inverseRotate3D(axisV, rotation)), semi);

    // Без доп. лучей полость не сэмплируется: единственный центральный
    // конус заодно цепляет кольцо грейзингом по мере роста апертуры
    bool hollow = voidR > 0.0 && extraRays >= 3;

    int idx = 0;
    if (!hollow)
        pts[idx++] = center;

    // Сплошные: вершины чуть внутри силуэта — луч с самой кромки
    // даёт нестабильный грейзинг. Полые: середина между пустотой и кромкой
    float rho = hollow ? 0.5 * (1.0 + voidR) : 0.85;

    vec3 axisL = semi.x >= semi.y && semi.x >= semi.z ? vec3(1.0, 0.0, 0.0)
               : semi.y >= semi.z                      ? vec3(0.0, 1.0, 0.0)
                                                       : vec3(0.0, 0.0, 1.0);
    vec3 axisW = rotate3D(axisL, rotation);
    vec2 v0 = vec2(dot(axisW, axisU), dot(axisW, axisV));

    // Источники развёрнуты на золотой угол друг от друга:
    // ступени пенумбры разных источников не совпадают по азимуту.
    // Проекция длинной оси вырождена (смотрим вдоль неё) — фаза нулевая
    float base = (dot(v0, v0) > 1e-6 ? atan(v0.y, v0.x) : 0.0)
               + u_ngonPhase + float(lightNodeId) * 2.399963;

    extraRays = min(extraRays, LIGHT_EXTRA_RAY_MAX - idx);
    // Константная граница + break: компилятор разворачивает цикл,
    // запись в pts остаётся регистровой
    for (int k = 0; k < 8; k++)
    {
        if (k >= extraRays) break;
        float ang = base + float(k) * 6.2831853 / float(extraRays);
        pts[idx++] = center + (axisU * (cos(ang) * halfW) + axisV * (sin(ang) * halfH)) * rho;
    }
    return idx;
}

// Оценка вклада источника в точку без марча:
// площадь × яркость / квадрат расстояния, компенсация деления на π,
// временное мерцание. Единая формула для ранжирования источников
// и для расчёта освещения — radiance берётся отсюда в обоих местах
vec3 lightPowerEstimate(int lightNodeId, int lightParamsStart, vec3 surfPos, out vec3 L, out float dist2)
{
    vec3 toLight = NODE_TRANSLATION(lightParamsStart) - surfPos;
    dist2 = dot(toLight, toLight);
    L = toLight / max(sqrt(dist2), 1e-6);

    vec3 lightAlbedo;
    int lightMaterialId;
    getNodeAlbedoAndMaterialId(lightParamsStart, lightAlbedo, lightMaterialId);
    Material lightMaterial = u_materialsList[lightMaterialId];

    // Площадь источника определяет суммарную мощность:
    // большая панель освещает сильно, маленький огонёк — слабо,
    // даже при одинаковой поверхностной яркости (emission)
    float lightRadius = getNodeApproxMaxSemiAxis(lightNodeId);

    vec3 radiance = lightAlbedo * max(lightMaterial.emission, 0.0);
    radiance *= max(lightRadius * lightRadius, 0.04); // пол для крошечных источников
    radiance /= max(dist2, 0.0625);

    // Компенсация деления на π в диффузном BRDF:
    // источник и освещаемая им поверхность становятся соизмеримы
    radiance *= 3.0 * u_LightPowerScale;
    radiance *= 1.0 + 0.04 * sin(u_time * 11.0 + float(lightNodeId) * 1.7);

    return radiance;
}

// Небо — виртуальный источник: конкурирует за кольцевые лучи
// той же мерой, что лампы. u_ambientLightIntensity перепрофилировано в яркость светила.
float pwSky = u_ambientLightIntensity * SKY_SUN_DISC_EXPOSURE * 0.3 * u_LightPowerScale; // калибровочная подборка



// Функция для расчета освещения поверхности с учетом материала и освещения для первичных лучей
vec3 shadeSurface(
    vec3 surfPos,
    vec3 viewDir,
    vec3 surfNormal,
    vec3 surfGeomNormal,
    vec3 surfAlbedo,
    Material material,
    float shadowScale,
    float ambientScale,
    bool skipSpecular,
    int nodeId
) {

    if(u_fastMode)
        return surfAlbedo;

    vec3 N = normalize(surfNormal);
    vec3 geomN = normalize(surfGeomNormal);

    // В вызывающем коде viewDir — направление текущего луча,
    // то есть от камеры к поверхности. Для BRDF нужен surface -> camera.
    vec3 V = normalize(-viewDir);

    float roughness = clamp(material.roughness, 0.04, 1.0);
    float metallic = clamp(material.metallic, 0.0, 1.0);
    float transmission = clamp(material.transmission, 0.0, 1.0);

    vec3 F0 = mix(vec3(0.04), surfAlbedo, metallic);

    // У стекла прямой непрозрачный diffuse-слой подавляется.
    float opaquePart = 1.0 - transmission;

    float ao = calcAO(surfPos + N * 0.004, N);

    // AO затемняет только локальное непрямое освещение,
    // но не уничтожает цвет окружения в контакте.
    float ambientVisibility = mix(1.0, ao, 0.65);

    vec3 directLight = vec3(0.0);

    float NdotV = max(dot(N, V), 0.0);
    float shadowAmount = clamp(shadowScale, 0.0, 1.0);

    int lightCount = min(u_lightCount, LIGHT_MAX_COUNT);

    // --- Ранжирование источников по оценке вклада ---
    // Доп. лучи площадной выборки получают только самые мощные источники.
    // Оценка марча не требует: трансляция, альбедо/материал, радиус.
    // Источники под поверхностью не ранжируются: их вклад идёт через
    // SSS и rim, которые марча не требуют
    const int TOPK_MAX = 4;
    float topPow[TOPK_MAX];
    topPow[0] = 0.0;
    topPow[1] = 0.0;
    topPow[2] = 0.0;
    topPow[3] = 0.0;

    int kEff = min(u_topLightCount, TOPK_MAX);

    for (int i = 0; i < lightCount; ++i)
    {
        int lightNodeId = u_lights[i];
        if (lightNodeId < 0 || lightNodeId >= u_nodesCount)
            continue;

        int lightParamsStart = int(u_nodes[lightNodeId].params.x);

        vec3 Le;
        float dist2e;
        vec3 radEst = lightPowerEstimate(lightNodeId, lightParamsStart, surfPos, Le, dist2e);
        float pw = max(radEst.r, max(radEst.g, radEst.b));

        float lightRadiusEst = getNodeApproxMaxSemiAxis(lightNodeId);
        float thetaEst = lightRadiusEst / max(sqrt(dist2e), 1e-4);

        // Источник виден краем диска, даже когда центр чуть за горизонтом
        if (pw < 0.001 || dot(N, Le) < -thetaEst)
            continue;

        // Вставка в отсортированный по убыванию массив.
        // Границы обоих циклов константны — компилятор разворачивает их,
        // индексация после развёртки статическая, массив остаётся в регистрах
        for (int s = 0; s < TOPK_MAX; s++)
        {
            if (pw > topPow[s])
            {
                for (int q = TOPK_MAX - 1; q > s; q--) topPow[q] = topPow[q - 1];
                topPow[s] = pw;
                break;
            }
        }
    }


    for (int s = 0; s < TOPK_MAX; s++)
    {
        if (pwSky > topPow[s])
        {
            for (int q = TOPK_MAX - 1; q > s; q--) topPow[q] = topPow[q - 1];
            topPow[s] = pwSky;
            break;
        }
    }

    // Ярких источников меньше kEff — хвост слотов нулевой,
    // порог пропускает всех видимых кандидатов.
    // Цепочка if вместо индексации: динамический индекс локального
    // массива отправляет его в VRAM, как и с регистрами
    float powCut = 1e30;
    if (kEff >= 4) powCut = topPow[3];
    else if (kEff == 3) powCut = topPow[2];
    else if (kEff == 2) powCut = topPow[1];
    else if (kEff == 1) powCut = topPow[0];

    // Как у ламп: база — один луч, кольцо — если небо прошло отбор
    int skyRays = 1 + ((pwSky >= powCut) ? min(u_areaRayCount, SKY_RAY_MAX - 1) : 0);

    // Толщина самого объекта не по направлению к источникам
    // thickV не зависит от лампы — один раз на пиксель,
    // и только если SSS активен: зонд стоит 8 маршей mapShadow
    float thickV = (material.subsurface > 0.001)
        ? probeThickness(surfPos, viewDir, 8.0)  // было 2.0: клип превращал плавный спад в обрыв+плато
        : 0.0;

    for (int i = 0; i <= lightCount; ++i)
    {
        // --- Виртуальный источник «небо» ---
        if (i == lightCount)
        {
            vec3 skyDirs[SKY_RAY_MAX];
            int sunCount;
            int nSky = skyRayDirections(N, skyRays, skyDirs, sunCount);
            if (nSky > 0)
            {
                vec3 sunDir = sunDirection();

                vec3 skySssVis = vec3(0.0);
                float skySssPath = 0.0;
                float skySssPathW = 0.0;
                vec3 sunVisAcc = vec3(0.0);

                float skyShadowBias = mix(0.03, 0.006, clamp(dot(N, sunDir), 0.0, 1.0));

                for (int s = 0; s < SKY_RAY_MAX; s++)
                {
                    if (s >= nSky) break;

                    vec3 Li = skyDirs[s];
                    bool isSunSample = (s < sunCount);

                    float coneAp   = isSunSample ? u_skySunAngRadius : SKY_CONE_RING;
                    float visDist  = isSunSample ? SKY_VIS_DIST_SUN   : SKY_VIS_DIST_RING;
                    bool fullMarch = isSunSample;

                    float ownLen = 0.0;
                    vec3 vis = skyVisibilityCone(
                        surfPos + surfGeomNormal * skyShadowBias,
                        Li, visDist, coneAp,
                        fullMarch, nodeId, ownLen
                    );

                    // AO и художественный ambientScale затемняют только рассеянное небо,
                    // не направленное солнце.
                    float skyAmbientAtten = isSunSample ? 1.0 : (ambientVisibility * ambientScale);
                    vis *= mix(vec3(1.0), vec3(skyAmbientAtten), shadowAmount);

                    if (material.subsurface > 0.001)
                    {
                        float wSss = 1.0 / float(nSky);
                        skySssVis += vis * wSss;
                        if (ownLen > 0.0) { skySssPath += ownLen * wSss; skySssPathW += wSss; }
                    }

                    if (isSunSample)
                        sunVisAcc += vis;

                    if (vis.r + vis.g + vis.b < 0.003)
                        continue;

                    float NdotLi = dot(N, Li);
                    float horizonFade = isSunSample
                        ? clamp(NdotLi / u_skySunAngRadius + 1.0, 0.0, 1.0)
                        : clamp(NdotLi / SKY_RING_SOFT_ANGLE + 1.0, 0.0, 1.0);
                    if (horizonFade <= 0.0)
                        continue;

                    vec3 Hi = normalize(V + Li);
                    float NdotHi = max(dot(N, Hi), 0.0);
                    float VdotHi = max(dot(V, Hi), 0.0);
                    vec3 Fi = fresnelSchlick(VdotHi, F0);
                    vec3 kDi = (vec3(1.0) - Fi) * (1.0 - metallic);
                    vec3 diffuse_i = kDi * surfAlbedo * opaquePart / PI;

                    float wrapAmt = 0.4 * clamp(material.subsurface, 0.0, 1.0);
                    float NdotLdiff_i = clamp((NdotLi + wrapAmt) / (1.0 + wrapAmt), 0.0, 1.0);

                    vec3 radiance_i = skyRadiance(Li, isSunSample);
                    // Солнце: направленный источник, вес 1 делится между сэмплами;
                    // кольца: нормированы на свой набор.
                    float wSky = isSunSample
                        ? (1.0 / float(sunCount))
                        : (SKY_AMBIENT_SCALE / max(float(nSky - sunCount), 1.0));

                    directLight += diffuse_i * NdotLdiff_i * radiance_i * vis * wSky * horizonFade;
                }

                // Спекуляр от диска светила — один аналитический блик по точному sunDir
                // с усреднённой видимостью треугольных сэмплов.
                if (!skipSpecular && sunCount > 0)
                {
                    vec3 meanSunVis = sunVisAcc / float(sunCount);
                    if (meanSunVis.r + meanSunVis.g + meanSunVis.b > 0.003)
                    {
                        vec3 Hsun = normalize(V + sunDir);
                        float NdotHsun = max(dot(N, Hsun), 0.0);
                        float VdotHsun = max(dot(V, Hsun), 0.0);
                        vec3 Fsun = fresnelSchlick(VdotHsun, F0);
                        float NdotLsun = max(dot(N, sunDir), 0.0);
                        float Dsun = D_GGX(NdotHsun, roughness);
                        float Gsun = G_Smith(NdotV, NdotLsun, roughness);
                        vec3 specular_sun = Dsun * Gsun * Fsun / max(4.0 * NdotV * NdotLsun, 0.001);

                        float latexSpecBoost =
                            1.0 + 12.0
                            * (1.0 - metallic)
                            * (1.0 - smoothstep(0.35, 0.75, roughness));
                        specular_sun *= latexSpecBoost;

                        vec3 radiance_sun = skyRadiance(sunDir, true);
                        float horizonFadeSun = clamp(NdotLsun / u_skySunAngRadius + 1.0, 0.0, 1.0);
                        directLight += specular_sun * radiance_sun * meanSunVis * horizonFadeSun;
                    }
                }

                // SSS от неба: в основном от направленного солнца; усредняем по вошедшим лучам.
                if (material.subsurface > 0.001 && skySssPathW > 0.0)
                {
                    const float SSS_TINT_DEPTH   = 0.5;
                    const float SSS_FRONT_WEIGHT = 0.6;
                    const float SSS_THIN_GLOW    = 0.7;
                    const float SSS_INTENSITY    = 0.5;
                    const float SSS_BACK_GATE_EXP = 2.0;
                    const float SSS_RELIEF       = 0.4;

                    float thick = skySssPath / skySssPathW;
                    vec3 sigma = -log(max(material.sssColor, vec3(1e-3))) / max(material.subsurface, 1e-3);
                    float gate = pow(clamp(dot(viewDir, sunDir), 0.0, 1.0), SSS_BACK_GATE_EXP);

                    vec3 Ns = normalize(mix(geomN, N, SSS_RELIEF));
                    vec3 bentL = normalize(Ns * 0.35 - sunDir);
                    float back = pow(clamp(dot(V, bentL), 0.0, 1.0), 3.0) * gate;
                    float wrap = pow(clamp((dot(Ns, sunDir) + 0.4) / 1.4, 0.0, 1.0), 2.0);

                    float effDepth = mix(thickV * SSS_TINT_DEPTH, thick, gate);
                    vec3 t1 = exp(-sigma * effDepth);
                    vec3 t2 = exp(-sigma * effDepth * 0.3333);
                    vec3 sssTint = mix(t1, t2, 0.5);
                    float thinBoost = mix(1.0, 0.4 + exp(-thickV * 2.0), SSS_THIN_GLOW);
                    float rimAtten = 0.35 + 0.65 * smoothstep(0.0, 0.35, abs(dot(Ns, V)));

                    vec3 sunRadiance = skyRadiance(sunDir, true);
                    vec3 sssLight = sunRadiance * sssTint
                        * (back + SSS_FRONT_WEIGHT * wrap * thinBoost)
                        * SSS_INTENSITY * rimAtten;
                    sssLight = min(sssLight, sunRadiance * 0.3);
                    directLight += sssLight * skySssVis;
                }
            }

            continue;
        }

        int lightNodeId = u_lights[i];
        
        if (lightNodeId < 0 || lightNodeId >= u_nodesCount)
            continue;

        int lightParamsStart = int(u_nodes[lightNodeId].params.x);

        vec3 L;
        float dist2;
        vec3 radiance = lightPowerEstimate(lightNodeId, lightParamsStart, surfPos, L, dist2);

        if (dist2 <= 0.00001)
            continue;

        float lightDistance = sqrt(dist2);
        float NdotLraw = dot(N, L);

        // Верхняя оценка вклада источника: shadow и NdotL только уменьшают его.
        // Ниже этого порога после ACES вклад неразличим — тень не считаем.
        // Порог консервативный: при желании поднимать до 0.003–0.005
        float pw = max(radiance.r, max(radiance.g, radiance.b));
        if (pw < 0.001)
            continue;

        // Контровой свет: граничное (грейзинговое) отражение источника позади.
        // До отсечения по NdotL; без марча (самозатенение силуэта)
        const float RIM_STRENGTH = 0.6; // яркость контура (0 — выкл)
        const float RIM_FALLOFF = 3.0; // ширина контура: больше — уже
        const float RIM_BACK_SHARP = 2.0; // насколько строго свет позади
        const float RIM_SIDE_SHARP = 1.5; // асимметрия: больше — контур жмётся к стороне источника
        {
            float rimGate = pow(clamp(dot(viewDir, L), 0.0, 1.0), RIM_BACK_SHARP);
            float rimMask = pow(1.0 - clamp(dot(N, V), 0.0, 1.0), RIM_FALLOFF);
            vec3 rimTint = mix(vec3(1.0), surfAlbedo, metallic);

            // Азимут: яркость спадает от стороны источника к противоположной.
            // Свет строго позади вырождается в ровное кольцо — это правильно
            vec3 Nv = N - V * dot(N, V);
            vec3 Lv = L - V * dot(L, V);
            float lenLv = length(Lv);

            // Вес азимута: свет почти на оси взгляда — направление
            // не определено, плавно откатываемся к ровному кольцу
            float azWeight = smoothstep(0.05, 0.3, lenLv);
            float cosAz = dot(Nv, Lv) / max(length(Nv) * lenLv, 1e-4);
            float rimSide = mix(1.0, clamp(cosAz, -1.0, 1.0) * 0.5 + 0.5, azWeight);
            rimSide = pow(rimSide, RIM_SIDE_SHARP);

            directLight += radiance * rimGate * rimMask * rimTint * rimSide
                * opaquePart * RIM_STRENGTH;
        }

        float NdotL = max(NdotLraw, 0.0);

        // SSS размывает терминатор: diffuse обтекает его на долю wrapAmt,
        // specular остаётся по честному NdotL
        float wrapAmt = 0.4 * clamp(material.subsurface, 0.0, 1.0);

        // Bias больше при скользящем свете, меньше при прямом —
        // убирает полосы самозатенения на диффузных поверхностях
        float shadowBias = mix(0.03, 0.006, clamp(NdotL, 0.0, 1.0));

        float lightRadius = getNodeApproxMaxSemiAxis(lightNodeId);
        // Угловой размер источника: отношение его радиуса к дистанции
        float theta = lightRadius / lightDistance;
        // Описанная сфера источника для раннего выхода конусов:
        // углы бокса выступают за полуоси, радиус с запасом
        float lightBoundR = length(getNodeInscribedSemiAxes(lightNodeId, lightParamsStart)) * 1.1;

        // Доп. лучи к вершинам n-угольника — только самым мощным источникам
        // (ранжирование выше); u_areaRayCount < 3 — центральный луч для всех
        int nExtra = (u_areaRayCount >= 3 && pw >= powCut) ? u_areaRayCount : 0;

        // Радиус пустоты источника из попиксельного кэша
        float voidR = 0.0;
        for (int li = 0; li < LIGHT_MAX_COUNT; li++)
        {
            if (li >= haloCount) break;
            if (haloNode[li] == lightNodeId)
            {
                voidR = haloVoidR[li];
                break;
            }
        }

        vec3 pts[LIGHT_EXTRA_RAY_MAX];
        int ptCount = lightSamplePoints(lightNodeId, surfPos, nExtra, voidR, pts);


        // Веса: центр несёт половину массы — он стабильнее краёв силуэта;
        // без центра вершины делят массу поровну.
        // pts[0] — центр, только если точек на одну больше числа доп. лучей
        bool hasCenter = (ptCount == nExtra + 1);

        int nRing = ptCount - (hasCenter ? 1 : 0);
        float wRing = nRing > 0 ? (hasCenter ? 0.5 : 1.0) / float(nRing) : 0.0;

        vec3 sssVis = vec3(0.0);  // взвешенная чужая видимость по всем сэмплам
        float sssPath = 0.0;      // взвешенный путь в своём теле (по вошедшим лучам)
        float sssPathW = 0.0;     // сумма весов вошедших лучей — нормировка sssPath

        // Константная граница цикла + break: компилятор разворачивает,
        // массив точек не уходит в local memory
        for (int s = 0; s < LIGHT_EXTRA_RAY_MAX; s++)
        {
            if (s >= ptCount) break;

            bool isCenter = hasCenter && s == 0;
            // Кольцевые конусы расширены до почти полного углового размера источника,
            // чтобы проекции вершин перекрывались и давали плавную пенумбру/SSS.
            float coneAp = isCenter ? max(theta, 0.001) : max(theta * 0.85, 0.001);
            // float w = isCenter ? (nExtra > 0 ? 0.5 : 1.0) : wRing;
            float w = 1.0 / float(ptCount);

            vec3 toP = pts[s] - surfPos;
            float d2s = dot(toP, toP);
            if (d2s < 1e-8) continue;
            vec3 Li = toP / sqrt(d2s);
            float NdotLi = dot(N, Li);

            // Мягкий терминатор: источник — не точка, а диск радиуса theta.
            // Точка поверхности, где центр источника чуть ниже горизонта,
            // но часть диска видна, всё ещё получает освещение.
            float horizonFade = clamp(NdotLi / max(theta, 1e-3) + 1.0, 0.0, 1.0);
            bool frontSample = horizonFade > 0.0;

            // SSS питается от полного набора лучей источника —
            // столько же, сколько у остальных задач, без привилегии s==0
            bool sssProbe = material.subsurface > 0.001;

            if (!frontSample && !sssProbe) continue;

            bool refr = false; int gNode = -1; float ownLen;
            
            vec3 vis = lightVisibilityCone(surfPos + surfGeomNormal * shadowBias, pts[s], coneAp,
                                           CONE_MAX_REFRACT, refr, gNode, 2.0,
                                           NODE_TRANSLATION(lightParamsStart), lightBoundR, false,
                                           nodeId, ownLen);

            vis = mix(vec3(1.0), vis, shadowAmount);

            if (sssProbe)
            {
                sssVis += vis * w; // Σw = 1: остаётся «долей неперекрытого источника»
                // Хорду усредняем только по вошедшим лучам: фронтальные
                // сэмплы с ownLen = 0 не должны тянуть толщу вниз
                if (ownLen > 0.0) { sssPath += ownLen * w; sssPathW += w; }
            }

            if (!frontSample) continue; // данные собраны, дальше только BRDF

            if (vis.r + vis.g + vis.b < 0.003) continue;

            vec3 Hi = normalize(V + Li);
            float NdotHi = max(dot(N, Hi), 0.0);
            float VdotHi = max(dot(V, Hi), 0.0);
            vec3 Fi = fresnelSchlick(VdotHi, F0);
            vec3 kDi = (vec3(1.0) - Fi) * (1.0 - metallic);
            vec3 diffuse_i = kDi * surfAlbedo * opaquePart / PI;
            float NdotLdiff_i = clamp((NdotLi + wrapAmt) / (1.0 + wrapAmt), 0.0, 1.0);

            // Для спекуляра отрицательный NdotLi физически недопустим,
            // поэтому clamp, но общий вклад сэмпла всё равно гасится horizonFade.
            float NdotLiPos = max(NdotLi, 0.0);

            vec3 specular_i = vec3(0.0);
            if (!skipSpecular)
            {
                float Di = D_GGX(NdotHi, roughness);
                float Gi = G_Smith(NdotV, NdotLiPos, roughness);
                specular_i = Di * Gi * Fi / max(4.0 * NdotV * NdotLiPos, 0.001);

                // Художественная компенсация слабого F0=0.04 у latex/пластика.
                // Работает только на гладких неметаллах; diffuse, SSS и металлы не усиливает.
                float latexSpecBoost =
                    1.0 + 12.0
                    * (1.0 - metallic)
                    * (1.0 - smoothstep(0.35, 0.75, roughness));

                specular_i *= latexSpecBoost;
            }

            // radiance уже содержит площадь, мерцание и power scale —
            // пересчитываем только дистанционный член
            vec3 radiance_i = radiance * (max(dist2, 0.0625) / max(d2s, 0.0625));

            directLight += (diffuse_i * NdotLdiff_i + specular_i * NdotLiPos) * radiance_i * vis * w;

        }

        // --- Настройки SSS ---
        const float SSS_TINT_DEPTH = 0.5; // насыщенность тона при фронтальном свете
        const float SSS_FRONT_WEIGHT = 0.6; // яркость фронтального рассеивания
        const float SSS_THIN_GLOW = 0.7; // подсветка тонких мест (0 — выкл)
        const float SSS_INTENSITY = 0.5; // общая сила SSS
        const float SSS_BACK_GATE_EXP = 2.0; // ширина зоны «источник позади»

        // Подповерхностное рассеивание считается до отсечения по NdotL:
        // свет проходит сквозь тело, и просвет виден как раз на теневой
        // стороне. Тень здесь не применяем — путь света лежит через
        // толщу, а не вдоль поверхности
        if (material.subsurface > 0.001)
        {
            // Средняя хорда по вошедшим лучам; ни один не вошёл — тела нет
            float thick = (sssPathW > 0.0) ? sssPath / sssPathW : 0.0;

            vec3 sigma = -log(max(material.sssColor, vec3(1e-3))) / max(material.subsurface, 1e-3);

            float gate = pow(clamp(dot(viewDir, L), 0.0, 1.0), SSS_BACK_GATE_EXP);

            // Рельеф в SSS разбавлен, не вырезан: отпечаток остаётся
            // на долю blend, энергия не уходит в clamp-нули куба
            const float SSS_RELIEF =  0.4; // 0 — геометрия, 1 — как было
            vec3 Ns = normalize(mix(geomN, N, SSS_RELIEF));

            vec3 bentL = normalize(Ns * 0.35 - L);
            float back = pow(clamp(dot(V, bentL), 0.0, 1.0), 3.0) * gate;
            // wrap — локально от Ns; общий NdotLraw не трогаем
            float wrap = pow(clamp((dot(Ns, L) + 0.4) / 1.4, 0.0, 1.0), 2.0);

            // Геометрический эталон глубины (ось взгляда): σ больше не
            // сокращается — subsurface скейлит и фронт, и контур.
            // Тонкие края светлеют сами — следить за дублем с thinBoost
            float effDepth = mix(thickV * SSS_TINT_DEPTH, thick, gate);
            // Плотные SSS: одна экспонента даёт узкую полосу затухания.
            // Вторая, втрое медленнее, — длинный мягкий «хвост»;
            // оттенок тот же (σ общая), меняется форма спада, не цвет
            vec3 t1 = exp(-sigma * effDepth);
            vec3 t2 = exp(-sigma * effDepth * 0.3333);
            vec3 sssTint = mix(t1, t2, 0.5);

            // Тонкие места светятся сильнее
            float thinBoost = mix(1.0, 0.4 + exp(-thickV * 2.0), SSS_THIN_GLOW);

            float rimAtten = 0.35 + 0.65 * smoothstep(0.0, 0.35, abs(dot(Ns, V)));

            vec3 sssLight = radiance * sssTint
                * (back + SSS_FRONT_WEIGHT * wrap * thinBoost)
                * SSS_INTENSITY * rimAtten;
            sssLight = min(sssLight, radiance * 0.3); // кепка ДО гейта: сила от источника, потом доступность

            // Порогов нет: непрозрачный окклюдер сам даёт ноль (светотень),
            // чужие SSS/стёкла тонируют просвет непрерывно

            // directLight += vec3(clamp(thick, 0.0, 1.0), sssVis.r, 0.0); // ОТЛАДКА: R — хорда 1:1, G — видимость
            // directLight += vec3(clamp(thick, 0.0, 1.0), sssVis.r, float(gVisFail) * 0.2); // ОТЛАДКА: B — причина

#if SSS_DEBUG_VIS
            // ОТЛАДКА:
            //   зелёный   — марш дошёл до источника (нет блока)
            //   R=id      — id блокера / (число узлов-1)
            //   G=t*0.2   — дистанция блока
            //   B=fail*0.2 — причина: 0.2=gate, 0.4=tint, 0.6=blocked, 0.8=budget steps, 1.0=budget segs
            vec3 debugColor;
            if (gVisFail == 0 && gVisBlockT < 0.0)
                debugColor = vec3(0.0, 1.0, 0.0);
            else
                debugColor = vec3(float(gVisBlocker + 1) / max(float(u_nodesCount - 1), 1.0),
                                  gVisBlockT * 0.2,
                                  float(gVisFail) * 0.2);
            directLight = debugColor;
#else
            directLight += sssLight * sssVis;
#endif
        }

    }

    return directLight;
}

// Дешёвая версия освещения для вторичных лучей:
// центральный конусный луч на источник, одно преломление,
// AO и GGX не рассчитываются — вторичный луч дорог сам по себе
vec3 shadeSurfaceCheap(
    vec3 surfPos,
    vec3 surfNormal,
    vec3 surfAlbedo,
    float transmission,
    bool ignoreGlass
) {
    vec3 N = normalize(surfNormal);
    float opaquePart = 1.0 - clamp(transmission, 0.0, 1.0);

    vec3 result = vec3(0.0);
    {
        // Полусферный остаток без марша: приближение рассеянного неба.
        vec3 skyDirUp   = N;
        vec3 skyDirDown = normalize(vec3(N.x, -0.3, N.z));
        vec3 hemiSky = mix(skyRadiance(skyDirDown, false), skyRadiance(skyDirUp, false), N.y * 0.5 + 0.5);
        result += hemiSky * surfAlbedo * opaquePart * 0.35;

        // Луч к светилу: дистанционный марш, без преломлений.
        vec3 sunDir = sunDirection();
        if (dot(sunDir, N) > 0.0)
        {
            float ownLen = 0.0;
            vec3 vis = skyVisibilityCone(surfPos + N * 0.02, sunDir, SKY_VIS_DIST_RING,
                                          u_skySunAngRadius, false, -1, ownLen);
            result += surfAlbedo * opaquePart * skyRadiance(sunDir, true) * vis * dot(N, sunDir) * 0.35;
        }
    }

    for (int i = 0; i < LIGHT_MAX_COUNT; i++)
    {
        if (i >= u_lightCount) break;

        int lightNodeId = u_lights[i];

        if (lightNodeId < 0 || lightNodeId >= u_nodesCount)
        {
            continue;
        }

        int lightParamsStart = int(u_nodes[lightNodeId].params.x);

        vec3 L;
        float dist2;
        vec3 radiance = lightPowerEstimate(lightNodeId, lightParamsStart, surfPos, L, dist2);

        if (dist2 <= 0.00001)
        {
            continue;
        }

        float NdotL = max(dot(N, L), 0.0);
        if (NdotL <= 0.0)
        {
            continue;
        }

        float lightRadius = getNodeApproxMaxSemiAxis(lightNodeId);
        float theta = lightRadius / max(sqrt(dist2), 1e-5);

        // Bias больше при скользящем свете, меньше при прямом —
        // убирает полосы самозатенения на диффузных поверхностях
        float shadowBias = mix(0.03, 0.006, clamp(NdotL, 0.0, 1.0));

        // Конус к центру источника: мягкая тень через покрытие конуса,
        // стекло преломляет один раз
        bool refrDummy; int glassDummy; float ownLenDummy; // Заглушки
        vec3 vis = lightVisibilityCone(
            surfPos + N * shadowBias,
            NODE_TRANSLATION(lightParamsStart),
            max(theta, 0.001),
            CONE_MAX_REFRACT_CHEAP,
            refrDummy,
            glassDummy,
            2.0,
            NODE_TRANSLATION(lightParamsStart),
            length(getNodeInscribedSemiAxes(lightNodeId, lightParamsStart)) * 1.1,
            ignoreGlass, 
            -1, 
            ownLenDummy
        );

        result += surfAlbedo
            * opaquePart
            * radiance
            * NdotL
            * vis;
    }

    return result;
}

vec3 traceCheapGlassSideRay(vec3 origin, vec3 dir, float coneAperture)
{
    float tSide = 0.02;
    const int maxSideSteps = 32;

    bool isGrazing = false;
    float prevDist = 9999.0;
    float minGrazingDist = 9999.0;
    float grazingConeRadius = 0.0;
    int grazingNodeId = -1;
    vec3 grazingPoint = origin;

    for (int i = 0; i < maxSideSteps; i++)
    {
        vec3 p = origin + dir * tSide;
        vec2 mapRes = mapBase(p);
        float d = mapRes.x;
        float coneRadius = max(tSide * coneAperture, 0.001);

        if (d < max(0.001, coneRadius * 0.08))
        {
            int nodeId = int(mapRes.y);
            int paramsStart = int(u_nodes[nodeId].params.x);

            vec3 hitAlbedo;
            int matId;
            getNodeAlbedoAndMaterialId(paramsStart, hitAlbedo, matId);
            Material matHit = u_materialsList[matId];

            int hitTexID;
            vec2 hitTexOffset;
            float hitTexRotation;
            vec2 hitTexOriginalSize;
            float hitTexTilingScale;

            bool hasTex = loadNodeTextureParams(
                paramsStart,
                hitTexID,
                hitTexOffset,
                hitTexRotation,
                hitTexOriginalSize,
                hitTexTilingScale
            );

            if (hasTex)
            {
                vec3 hitTexColor = sampleSDFTexture(
                    u_textures,
                    float(hitTexID),
                    p,
                    hitTexOriginalSize,
                    hitTexOffset,
                    hitTexRotation,
                    hitTexTilingScale,
                    NODE_TRANSLATION(paramsStart),
                    NODE_ROTATION(paramsStart),
                    coneRadius
                );
                hitAlbedo = ApplyHueOrColorize(hitTexColor, hitAlbedo);
            }

            if (matHit.transmission > 0.001)
            {
                tSide += max(0.03, coneRadius * 0.5);
                prevDist = d;
                continue;
            }

            if (matHit.emission > 0.0)
            {
                return hitAlbedo * matHit.emission;
            }

            return hitAlbedo;
        }

        if (d < coneRadius)
        {
            if (!isGrazing)
            {
                isGrazing = true;
                grazingNodeId = int(mapRes.y);
                minGrazingDist = d;
                grazingConeRadius = coneRadius;
                grazingPoint = p;
            }
            else
            {
                minGrazingDist = min(minGrazingDist, d);
            }
        }

        if (isGrazing && d > prevDist && d > coneRadius)
        {
            int paramsStart = int(u_nodes[grazingNodeId].params.x);

            vec3 hitAlbedo;
            int matId;
            getNodeAlbedoAndMaterialId(paramsStart, hitAlbedo, matId);
            Material matHit = u_materialsList[matId];

            int hitTexID;
            vec2 hitTexOffset;
            float hitTexRotation;
            vec2 hitTexOriginalSize;
            float hitTexTilingScale;

            bool hasTex = loadNodeTextureParams(
                paramsStart,
                hitTexID,
                hitTexOffset,
                hitTexRotation,
                hitTexOriginalSize,
                hitTexTilingScale
            );

            if (hasTex)
            {
                vec3 hitTexColor = sampleSDFTexture(
                    u_textures,
                    float(hitTexID),
                    grazingPoint,
                    hitTexOriginalSize,
                    hitTexOffset,
                    hitTexRotation,
                    hitTexTilingScale,
                    NODE_TRANSLATION(paramsStart),
                    NODE_ROTATION(paramsStart),
                    grazingConeRadius
                );
                hitAlbedo = ApplyHueOrColorize(hitTexColor, hitAlbedo);
            }

            float coverage = clamp(1.0 - (minGrazingDist / max(grazingConeRadius, 0.001)), 0.0, 1.0);

            vec3 hitColor = (matHit.emission > 0.0) ? (hitAlbedo * matHit.emission) : hitAlbedo;

            float bgAlpha;
            vec3 bgColor = getBackground(origin, dir, bgAlpha, false);

            return mix(bgColor, hitColor, coverage);
        }

        if (tSide > 10.0) break;

        prevDist = d;
        tSide += isGrazing ? max(d * 0.5, 0.01) : max(d, coneRadius * 0.5);
    }

    float bgAlpha;
    return getBackground(origin, dir, bgAlpha, false);
}

// Функция для тонального отображения цвета с помощью ACES Filmic
// Эта функция обеспечивает более реалистичное отображение ярких цветов 
// и сохраняет детали в тенях
vec3 ACESFilm(vec3 x)
{
    if(u_outputLinear) return x;
    const float a = 2.51f;
    const float b = 0.03f;
    const float c = 2.43f;
    const float d = 0.59f;
    const float e = 0.14f;
    return clamp((x*(a*x+b))/(x*(c*x+d)+e), 0.0, 1.0);
}

// Функция тонального отображения с сохранением оттенка
// Нужна для того, чтобы очень яркий теплый или холодный свет не выбеливался полностью
vec3 ACESFilmPreserveHue(vec3 c)
{
    if (u_outputLinear)
        return c;
    float peak = max(max(c.r, c.g), c.b);

    if (peak <= 1.0) return ACESFilm(c);

    vec3 chroma = c / peak;
    float mappedPeak = ACESFilm(vec3(peak)).r;
    return chroma * mappedPeak;
}

// Единый hue-preserving путь: нет шва на peak=1 и нет десатурационной
// каймы в зоне ореола — desat включается только в HDR-высветлениях
vec3 ACESFilmToneMap(vec3 c)
{
    if (u_outputLinear)
        return c;
    float peak = max(max(c.r, c.g), c.b);
    if (peak <= 1e-5) return ACESFilm(c);
    vec3 chroma = c / peak;
    float mappedPeak = ACESFilm(vec3(peak)).r;
    float desat = smoothstep(1.0, 8.0, peak) * 0.85;
    return clamp(mix(chroma * mappedPeak, vec3(mappedPeak), desat), 0.0, 1.0);
}



void main()
{
    // Субпиксельный джиттер: сид 0 — эталон (центр пикселя, старое поведение),
    // сид > 0 — смещение в пределах пикселя; кадры с разными сидами
    // усредняются на хосте в чистый AA
    vec2 jitter = vec2(0.0);
    if (u_sampleSeed > 0)
    {
        vec2 seed = gl_FragCoord.xy + vec2(float(u_sampleSeed) * 17.13,
                                           float(u_sampleSeed) * 91.71);
        // Страты: сетка sx × sy ≥ M, кадр k занимает свою ячейку,
        // рандом — внутри ячейки. Слипания сэмплов нет при любом M
        int sx = int(ceil(sqrt(float(max(u_sampleCount, 1)))));
        int sy = (max(u_sampleCount, 1) + sx - 1) / sx;
        ivec2 cell = ivec2(u_sampleSeed % sx, u_sampleSeed / sx);
        vec2 jrand = vec2(rand(seed), rand(seed + 19.19));
        jitter = (vec2(cell) + jrand) / vec2(float(sx), float(sy)) - 0.5;
    }
    vec2 uv = v_uv * 2.0 - 1.0 + jitter * (2.0 / u_resolution);
    uv.x *= u_resolution.x / u_resolution.y;

    // Вычисляем фокусное расстояние камеры на основе угла обзора по вертикали
    float fovRad = radians(u_fovYDeg);
    float focal = 1.0 / tan(0.5 * fovRad);
    // Свободная камера
    // Луч исходит из позиции камеры и направлен в направлении, 
    // определяемом координатами пикселя и джиттером
    vec3 rayOrigin = u_camPos;
    // Нормализуем направление луча, чтобы его длина была 1,
    // что важно для правильного рендеринга и освещения
    // Направление луча формируется из координат uv и фокусного расстояния
    vec3 rayDir = normalize(vec3(uv, -focal));
    // Поворачиваем направление луча в соответствии с ориентацией камеры
    rayDir.yz *= rotate(u_camRot.x);
    rayDir.xz *= rotate(u_camRot.y);
    rayDir.xy *= rotate(u_camRot.z);

    // Ось экранной горизонтали в мировых координатах —
    // нужна анаморфной полосе ореола
    vec3 camRight = vec3(1.0, 0.0, 0.0);
    camRight.yz *= rotate(u_camRot.x);
    camRight.xz *= rotate(u_camRot.y);
    camRight.xy *= rotate(u_camRot.z);
    
    // Пустая сцена: реймарчинг не нужен — выводим только фон
    if (u_nodesCount <= 0)
    {
        float bgAlphaEmpty;
        vec3 bgColEmpty = getBackground(rayOrigin, rayDir, bgAlphaEmpty, true);
        fragColor = vec4(ACESFilmToneMap(bgColEmpty), bgAlphaEmpty);
        if(!u_outputLinear)
        {
            float dith = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453);
            fragColor.rgb = clamp(fragColor.rgb + (dith - 0.5) * (1.5 / 255.0), 0.0, 1.0);
        }
        return;
    }

    float hitAlpha = 0.0;

    int maxSteps = u_fastMode == true ? 64 : 256; // В быстром режиме уменьшаем число шагов первичного прохода

    // t - расстояние, пройденное вдоль первичного луча от камеры
    // Стартуем не строго с нуля, чтобы избежать лишних пограничных артефактов около камеры
    float t = 0.01;
    float prevT = 0.0;

    vec3 point;
    vec2 mapData;

    gReachMin = rayOrigin; gReachMax = rayOrigin;

    gVisFail = 0; // ОТЛАДКА: причина нулевой видимости светового марша
    gVisBlocker = -2;
    gVisBlockT = -1.0;

    // Параметр раскрытия конуса для первичного луча.
    // Чем больше значение, тем раньше луч начинает "видеть" тонкие и удалённые детали,
    // но тем выше риск преждевременных касаний по мелкой геометрии.
    float primaryConeAperture = u_fastMode ? 0.0008 : 0.0005;

    // Текущий радиус конуса на дистанции t.
    // В начале почти нулевой, затем плавно растёт по мере удаления от камеры.
    float primaryConeRadius = 0.0;

    // Накопленное свечение от скольжения конуса вдоль источников
    vec3 primaryGlow = vec3(0.0);

    // Апертура ореола: числитель ~ диаметр ореола в экранных пикселях.
    // 8.0 давало ~4 px радиуса (тонкая обводка);
    // 32.0 — ~16 px радиуса, читаемый линзовый ореол
    float glowAperture = (32.0*2) / (focal * u_resolution.y);
    initHaloLights();
    resetHaloSlots();
    
    for (int i = 0; i < maxSteps; i++)
    {
        // Текущая точка вдоль луча
        point = rayOrigin + rayDir * t;

        // Получаем расстояние до ближайшей поверхности и id ближайшего узла
        mapData = mapBase(point);
        float dist = mapData.x;

        // На текущей дистанции луч уже не бесконечно тонкий:
        // его эффективный радиус растёт пропорционально расстоянию от камеры
        primaryConeRadius = t * primaryConeAperture;

        // Порог попадания теперь зависит не только от абсолютного epsilon,
        // но и от толщины конуса. Это позволяет первичному лучу работать
        // согласованно со вторичными cone-tracing проходами.
        float hitThreshold = max(0.0004, primaryConeRadius * 0.05);

        // Если SDF-дистанция меньше порога конуса, считаем, что луч достиг поверхности
        if (dist < hitThreshold)
        {
            hitAlpha = 1.0;
            int hitNode = int(mapData.y);
            noteReach(point);
            // источник уже в слоте — его ореол добавит финализация по перигею
            if (!u_fastMode && (hitNode >= 0 && !haloSlotTracks(hitNode)))
            {
                int hnps = int(u_nodes[hitNode].params.x);
                if (u_materialsList[NODE_MATERIAL_ID(hnps)].emission > 0.0)
                    primaryGlow += grazingGlowColor(hitNode, 0.0,
                        t * glowAperture * (0.5 + getNodeApproxMaxSemiAxis(hitNode)));
            }
            break;
        }

        // Ограничение дальности первичного прохода,
        // чтобы луч не маршировал бесконечно далеко в пустоту
        if (t > 10.0*5.0)
        {
            break;
        }
        float stepSize = 0.0;
        if(!u_fastMode)
        {
            HaloStep halo = updateHaloTracking(point, t, glowAperture);
            primaryGlow += halo.glow;
            // мелкие шаги только в зоне ореола и на подходе к ней
            stepSize = halo.nearHalo ? max(dist * 0.5, 0.002) : max(dist, 0.002);
        }
        else
        {
            stepSize = max(dist, primaryConeRadius * 0.3);
        }
        prevT = t; 
        t += stepSize;
    }
    if(!u_fastMode)
        primaryGlow += finalizeHaloSlots();


    if (hitAlpha > 0.5)
    {
        // Уточнение точки попадания: cone-tracing порог оставляет точку
        // на расстоянии до hitThreshold от поверхности, что даёт кольцевой
        // бандинг, растущий с удалением от камеры. Бинарным поиском
        // сходимся к абсолютному эпсилону между последней внешней точкой
        // и точкой хита.
        float tA = prevT;
        float tB = t;
        for (int i = 0; i < 8; i++)
        {
            float tm = 0.5 * (tA + tB);
            float dm = mapBase(rayOrigin + rayDir * tm).x;
            if (dm < 0.0005)
                tB = tm;
            else
                tA = tm;
        }
        t = 0.5 * (tA + tB);
        point = rayOrigin + rayDir * t;
        mapData = mapBase(point); // важно: пересчитать, чтобы NodeId внизу соответствовал уточнённой точке

        int hitNodeId0 = int(mapData.y);
        int hitParams0 = int(u_nodes[hitNodeId0].params.x);

        // Адаптивный эпсилон: при активной карте высот градиент усредняется
        // на масштабе текселя — убирает фасеточность бликов.
        // Без карты высот поведение прежнее (0.0001)
        float normalEps = 0.0001;
        {
            int hmTex; vec2 hmOff; float hmRot; vec2 hmSize; float hmTiling; float hmDepth;
            if (loadNodeHeightMapParams(hitParams0, hmTex, hmOff, hmRot, hmSize, hmTiling, hmDepth))
            {
                float texelWorld = abs(NODE_SCALE(hitParams0))
                                 / max(hmTiling * max(hmSize.x, hmSize.y), 1e-4);
                normalEps = clamp(texelWorld * 0.5, 0.0001, 0.02);
            }
        }
        vec3 normal = calcNormalGlobalEps(point, normalEps);
        
        if(u_fastMode == true)
        {   
            //Будет вставлено внешним кодом
            // ___FAST_RENDER_CODE___
        }
        else
        {
            // === Инициализация состояния путей (Path Tracing) ===
            vec3 accumulatedColor = vec3(0.0);
            vec3 throughput = vec3(1.0); // Энергия луча (100% на старте)
            
            vec3 currentOrigin = point;
            vec3 currentDir = rayDir;
            vec3 currentNormal = normal;
            vec3 geomNormal = normal;

            int NodeId = int(mapData.y);
            int paramsStart = int(u_nodes[NodeId].params.x);

            int normalMapTexID;
            vec2 normalMapOffset;
            float normalMapRotation;
            vec2 normalMapOriginalSize;
            float normalMapTilingScale;

            bool normalMapExists = loadNodeNormalMapParams(paramsStart, 
                                                            normalMapTexID, normalMapOffset, 
                                                            normalMapRotation, normalMapOriginalSize, 
                                                            normalMapTilingScale);
            if(normalMapExists)
                {
                    vec3 objNormal = triplanarNormal(u_textures, float(normalMapTexID), currentOrigin, currentNormal, normalMapOriginalSize, normalMapOffset, normalMapRotation, normalMapTilingScale,
                                                NODE_TRANSLATION(paramsStart), NODE_ROTATION(paramsStart), blendSharpness);
                    
                    // Смешиваем нормаль из карты с базовой нормалью, полученной от SDF
                    currentNormal = normalize(mix(currentNormal, objNormal, 1.0));
                }
            
            // Флаг нахождения внутри объекта
            bool isInside = false;
            vec3 insideEntry = vec3(0.0);
            vec3 insideAlbedo = vec3(1.0);
            Material insideMat;
            float insideMaxLen = 1e30;

            // Накопленный радиус для прозрачных объектов
            float persistentConeRadius = 0.0; 

            int glassSideRayBudget = max(2, min(u_maxReflectCount/2, 8));

            // Накопленная дистанция от камеры для атмосферной перспективы
            float pathAcc = t;

            // Цикл отскоков (r = 0 это первичное попадание)
            for (int r = 0; r < u_maxReflectCount; r++)
            {
                // 1. Получаем данные объекта в текущей точке
                vec2 mapRes = mapBase(currentOrigin);
                NodeId = int(mapRes.y);
                paramsStart = int(u_nodes[NodeId].params.x);

                vec3 albedo; int materialId;
                getNodeAlbedoAndMaterialId(paramsStart, albedo, materialId);
                Material material = u_materialsList[materialId];

                int albedoTexID;
                vec2 texOffset;
                float texRotation;
                vec2 texOriginalSize;
                float texTilingScale;

                bool textureExist = loadNodeTextureParams(paramsStart, albedoTexID, texOffset, texRotation, texOriginalSize, texTilingScale);

                if (textureExist)
                {
                    vec3 texColor;
                    texColor = triplanarColor(
                        u_textures,
                        float(albedoTexID),
                        currentOrigin,
                        currentNormal,
                        texOriginalSize,
                        texOffset,
                        texRotation,
                        texTilingScale,
                        NODE_TRANSLATION(paramsStart),
                        NODE_ROTATION(paramsStart),
                        blendSharpness
                    );

                    // Применяем логику тонирования/колоризации текстуры
                    albedo = ApplyHueOrColorize(texColor, albedo);
                }
                
                float emission = material.emission;
                
                if (emission > 0.0)
                {
                    float thick = probeLightThickness(currentOrigin, currentDir, 3.0);
                    float core = 1.0 - exp(-thick * 2.0);
                    core = 0.25 + 0.75 * core;  // край не темнее 25%
                    core *= 0.5 + 0.5 * smoothstep(0.0, 0.04, abs(dot(currentNormal, -currentDir)));
                    accumulatedColor += throughput * albedo * emission * core;
                    break;
                }

                float roughness = material.roughness;
                float metallic = material.metallic;
                float transmission = material.transmission;
                float ior = material.ior;
                float subsurface = material.subsurface;

                // Базовый F0 (0.04 для неметаллов)
                vec3 F0 = mix(vec3(0.04), albedo, metallic);

                bool isGlass = transmission > 0.001;
                bool isPhysicalGlass = isGlass && u_physGlass;
                bool isHologramGlass = isGlass && !u_physGlass;
                bool doRefract = isGlass;

                // 2. РАСЧЕТ ПРЯМОГО ОСВЕЩЕНИЯ (прямое освещение считается полным проходом только для первичной точки)
                if (r==0)
                {
                    vec3 shadedColor = shadeSurface(
                        currentOrigin,
                        currentDir,
                        currentNormal,
                        geomNormal,
                        albedo,
                        material,
                        1.0,     // shadowScale как в оригинале
                        1.0,     // ambientScale как в оригинале
                        false,    // specular оставить
                        NodeId
                    );

                    accumulatedColor += throughput * applyAerialPerspective(shadedColor, t, rayDir);
                }

                // 3. ВЫБОР СЛЕДУЮЩЕГО НАПРАВЛЕНИЯ (Отражение, Стекло или Голограмма)
                float cosTheta = min(max(dot(currentNormal, -currentDir), 0.0), 1.0);

                vec3 F = fresnelSchlick(cosTheta, F0);
                vec3 Fblend = clamp(
                    mix(F, sqrt(clamp(F, 0.0, 1.0)), 0.55),
                    0.0,
                    1.0
                );

                vec3 nextDir;
                float currentSign = 1.0;



                if (doRefract)
                {
                    if (isPhysicalGlass)
                    {
                        // Два дешёвых побочных отражающих луча только на внешней границе стекла.
                        // Основной путь при этом остаётся преломляющим.
                        if (!isInside && glassSideRayBudget > 0)
                        {
                            vec3 reflectDir0 = normalize(reflect(currentDir, currentNormal));

                            // + фаза кадра: иначе смена сида не даёт новой информации —
                            // накопление усредняло бы один и тот же шум
                            vec3 jitter = randomSpherePoint(v_uv
                                + vec2(float(r) * 1.37, float(NodeId) * 0.73)
                                + vec2(cos(u_ngonPhase), sin(u_ngonPhase)) * 7.31
                                + vec2(float(u_sampleSeed) * 0.618, float(u_sampleSeed) * 0.317));

                            vec3 reflectDir1 = normalize(reflectDir0 + jitter * max(roughness, 0.02) * 0.20);

                            // float sideConeAperture = max(roughness * 0.20, 0.01);
                            float sideConeAperture = max(roughness * 0.20, 0.01);

                            vec3 sideColor0 = traceCheapGlassSideRay(
                                currentOrigin + currentNormal * 0.01,
                                reflectDir0,
                                sideConeAperture
                            );
                            accumulatedColor += throughput * Fblend * sideColor0 * 0.65;

                            if (glassSideRayBudget > 1)
                            {
                                vec3 sideColor1 = traceCheapGlassSideRay(
                                    currentOrigin + currentNormal * 0.01,
                                    reflectDir1,
                                    sideConeAperture
                                );
                                accumulatedColor += throughput * Fblend * sideColor1 * 0.35;
                                glassSideRayBudget -= 2;
                            }
                            else
                            {
                                glassSideRayBudget -= 1;
                            }
                        }

                        float eta = isInside ? ior : (1.0 / ior);
                        vec3 refractedDir = refract(currentDir, currentNormal, eta);

                        if (length(refractedDir) < 0.001)
                        {
                            nextDir = reflect(currentDir, currentNormal);
                            throughput *= Fblend;
                            persistentConeRadius = 0.0;
                        }
                        else
                        {
                            nextDir = refractedDir;
                            throughput *= (1.0 - Fblend);
                            // Поглощение по пути взгляда: вход запоминаем,
                            // выход гасит throughput — та же пара сигм,
                            // что в маршах к источникам
                            if (!isInside)
                            {
                                insideEntry = currentOrigin;
                                insideAlbedo = albedo;
                                insideMat = material;
                                // Точка экстинкции: дальше носить нечего.
                                // SSS-член вырождается ровно в subsurface
                                float meanLogG = dot(-log(max(insideAlbedo, vec3(1e-3))), vec3(1.0 / 3.0));
                                float sigVBar = meanLogG * clamp(insideMat.transmission, 0.0, 1.0);
                                // Точка экстинкции — по САМОМУ ЖИВУЧЕМУ каналу:
                                // селективное поглощение отдаёт всю энергию выжившему,
                                // стоп по средней сигме резал его в чистый чёрный
                                vec3 sigV0 = -log(max(insideAlbedo, vec3(1e-3))) * clamp(insideMat.transmission, 0.0, 1.0);
                                if (insideMat.subsurface > 0.001)
                                    sigV0 += -log(max(insideMat.sssColor, vec3(1e-3))) / max(insideMat.subsurface, 1e-3);
                                float sigVMin = min(sigV0.r, min(sigV0.g, sigV0.b));
                                insideMaxLen = (sigVMin > 1e-4) ? 4.6 / sigVMin : 1e30;
                            }
                            isInside = !isInside;
                        }
                    }
                    else if (isHologramGlass)
                    {
                        nextDir = currentDir;

                        if (!isInside)
                        {
                            vec3 transparentColor = mix(vec3(1.0), albedo, transmission);
                            throughput *= (1.0 - F) * transparentColor;
                        }

                        isInside = !isInside;
                    }
                }
                else
                {
                    nextDir = reflect(currentDir, currentNormal);
                
                    // 1.0 для гладких материалов, около 0.12 для roughness >= 0.9.
                    float roughReflection = mix(
                        1.0,
                        0.12,
                        smoothstep(0.35, 0.90, roughness)
                    );
                
                    // Металл сохраняет отражение; неметалл с высокой roughness его теряет.
                    float metallicReflection = mix(roughReflection, 1.0, metallic);
                
                    throughput *= F * metallicReflection;
                
                    // Размывание secondary reflection в cone tracing.
                    persistentConeRadius = max(
                        persistentConeRadius,
                        roughness * 0.03
                    );
                }

                nextDir = normalize(nextDir);
                currentSign = isInside ? -1.0 : 1.0;

                // 4. ВТОРИЧНЫЙ ПУСК ЛУЧА (Cone Tracing со Скользящим Флагом)
                float tBounce = 0.01; 
                bool hit = false;
                vec3 nextPoint;

                // Для преломления (голограмма/чистое стекло) конус узкий, для матового отражения - расширяется
                float coneAperture = doRefract ? 0.0 : roughness * 0.5;
                // float coneAperture = roughness * 0.5;

                // Переменные логики "Скользящего Конуса" (Grazing Rays)
                bool isGrazing = false;
                float prevDist = 9999.0;
                int grazingNodeId = -1;
                float minGrazingDist = 9999.0;
                float grazingConeRadius = 0.0;

                for (int i = 0; i < maxSteps; i++)
                {
                    nextPoint = currentOrigin + nextDir * tBounce;
                    
                    vec2 mapResNew = mapBase(nextPoint);
                    float d = mapResNew.x * currentSign; 

                    // Поглощён в теле: промах соберёт фон через throughput —
                    // обнуляем его, вклад нулевой при любом продолжении
                    if (isInside && isPhysicalGlass && tBounce > insideMaxLen)
                    {
                        throughput = vec3(0.0);
                        break;
                    }

                    float coneRadius = persistentConeRadius + (tBounce * coneAperture);

                    // 4.1 Твердое пересечение (Конец пути луча для этого отскока)
                    if (d < max(0.001, coneRadius * 0.1))
                    {
                        int NodeIdHit = int(mapResNew.y);
                        noteReach(nextPoint);
                        int paramsStartHit = int(u_nodes[NodeIdHit].params.x);

                        vec3 hitAlbedo;
                        int matIdHit;
                        getNodeAlbedoAndMaterialId(paramsStartHit, hitAlbedo, matIdHit);
                        Material matHit = u_materialsList[matIdHit];

                        int hitAlbedoTexID;
                        vec2 hitTexOffset;
                        float hitTexRotation;
                        vec2 hitTexOriginalSize;
                        float hitTexTilingScale;
                        bool hitTextureExist = loadNodeTextureParams(
                            paramsStartHit,
                            hitAlbedoTexID,
                            hitTexOffset,
                            hitTexRotation,
                            hitTexOriginalSize,
                            hitTexTilingScale
                        );

                        if (hitTextureExist)
                        {
                            vec3 hitTexColor = sampleSDFTexture(
                                u_textures,
                                float(hitAlbedoTexID),
                                nextPoint,
                                hitTexOriginalSize,
                                hitTexOffset,
                                hitTexRotation,
                                hitTexTilingScale,
                                NODE_TRANSLATION(paramsStartHit),
                                NODE_ROTATION(paramsStartHit),
                                coneRadius
                            );
                            hitAlbedo = ApplyHueOrColorize(hitTexColor, hitAlbedo);
                        }

                        // Выход из своей среды: хит — поверхность выхода.
                        // Гасим по длине хорды и пропускаем луч наружу,
                        // не считая «попаданием»
                        if (isInside && isPhysicalGlass && NodeIdHit == NodeId)
                        {

                            // throughput *= vec3(1.0, 0.0, 0.0); // ОТЛАДКА: выход из среды должен краснить всё сквозное видение
                            
                            float pathLen = length(nextPoint - insideEntry);
                            vec3 sigV = -log(max(insideAlbedo, vec3(1e-3))) * clamp(insideMat.transmission, 0.0, 1.0);
                            if (insideMat.subsurface > 0.001)
                                sigV += -log(max(insideMat.sssColor, vec3(1e-3))) / max(insideMat.subsurface, 1e-3);
                            throughput *= exp(-sigV * pathLen);

                            isInside = false;
                            currentSign = 1.0;
                            tBounce += max(0.01, d * 2.0);
                            continue;
                        }

                        bool hitIsMediumBoundary = (matHit.transmission > 0.001);
                        if (matHit.emission > 0.0)
                        {
                            float hitDist = pathAcc + tBounce;
                            accumulatedColor += throughput * applyAerialPerspective(hitAlbedo * matHit.emission, hitDist, nextDir);
                            hit = true;
                            break;
                        }

                        if (hitIsMediumBoundary)
                        {
                            hit = true;
                            break;
                        }

                        vec3 hitNormal = calcNormalGlobal(nextPoint) * currentSign;

                        vec3 hitColor = shadeSurfaceCheap(
                            nextPoint,
                            hitNormal,
                            hitAlbedo,
                            matHit.transmission,
                            r>1
                        );

                        float hitDist = pathAcc + tBounce;
                        accumulatedColor += throughput * applyAerialPerspective(hitColor, hitDist, nextDir);
                        hit = true;
                        break;
                    }

                    // Эффективный радиус скольжения: для источников — расширенная аура
                    // ореола с экранно-постоянной шириной (t — дистанция первичного
                    // попадания). У гладких отражений и преломлений конус слишком тонок
                    // для cone-свечения, поэтому источники получают второй радиус
                    float effRadius = coneRadius;
                    {
                        int nid = int(mapResNew.y);
                        float haloRadius = (t + tBounce) * glowAperture;
                        if (nid >= 0 && d < haloRadius)
                        {
                            int nps = int(u_nodes[nid].params.x);
                            if (u_materialsList[NODE_MATERIAL_ID(nps)].emission > 0.0)
                                effRadius = max(coneRadius, haloRadius);
                        }
                    }

                    // 4.2 Логика мягкого конуса: Поднятие флага (вход в ауру)
                    if (d < effRadius)
                    {
                        if (!isGrazing) 
                        {
                            isGrazing = true;
                            grazingNodeId = int(mapResNew.y);
                            minGrazingDist = d;
                            grazingConeRadius = coneRadius;
                        } 
                        else 
                        {
                            // Обновляем минимальную дистанцию (ищем перигей)
                            minGrazingDist = min(minGrazingDist, d);
                        }
                    }

                    // 4.3 Опускание флага (Выход из ауры)
                    if (isGrazing && d > prevDist && d > grazingConeRadius)
                    {
                        isGrazing = false; // Опускаем флаг

                        // Вычисляем процент перекрытия конуса
                        float coverage = clamp(1.0 - (minGrazingDist / grazingConeRadius), 0.0, 1.0);
                        
                        int gParamsStart = int(u_nodes[grazingNodeId].params.x);
                        vec3 gAlbedo; int gMatId;
                        getNodeAlbedoAndMaterialId(gParamsStart, gAlbedo, gMatId);
                        Material gMat = u_materialsList[gMatId];

                        int gTexID;
                        vec2 gTexOffset;
                        float gTexRotation;
                        vec2 gTexOriginalSize;
                        float gTexTilingScale;
                        bool gTextureExist = loadNodeTextureParams(gParamsStart, gTexID, gTexOffset, gTexRotation, gTexOriginalSize, gTexTilingScale);

                        if (gTextureExist) {
                            vec3 gTexColor = sampleSDFTexture(
                                u_textures, 
                                float(gTexID), 
                                nextPoint, 
                                gTexOriginalSize, 
                                gTexOffset, 
                                gTexRotation, 
                                gTexTilingScale, 
                                NODE_TRANSLATION(gParamsStart), 
                                NODE_ROTATION(gParamsStart), 
                                grazingConeRadius
                            );
                            gAlbedo = ApplyHueOrColorize(gTexColor, gAlbedo);
                        }

                        if (gMat.emission > 0.0 && r <=2)
                        {
                            // Свечение источника по доле перекрытия конуса.
                            // grazingGlowColor считает отдельное перекрытие для RGB-каналов,
                            // поэтому край ореола получает лёгкую хроматическую кайму.
                            vec3 glowColor = grazingGlowColor(
                                grazingNodeId,
                                minGrazingDist,
                                grazingConeRadius
                            );

                            accumulatedColor += throughput * glowColor;

                            // Затухание пути остаётся скалярным: цветовая кайма относится
                            // к рассеянию в линзе, а не к поглощению энергии луча источником.
                            throughput *= (1.0 - coverage * 0.5);
                        }
                        else
                        {
                            // Мягкое скольжение по краю обычного объекта
                            accumulatedColor += throughput * gAlbedo * coverage;
                            throughput *= (1.0 - coverage); // Твердый объект блокирует луч сильнее
                        }

                        // Если энергия иссякла при прохождении через ауры - прекращаем марчинг
                        if (max(throughput.r, max(throughput.g, throughput.b)) < 0.01) break;
                    }

                    if (tBounce > 10.0) break;

                    prevDist = d;
                    // Если мы "чиркаем" объект, делаем шаги мельче для точности
                    tBounce += isGrazing ? max(d * 0.5, 0.01) : max(d, coneRadius * 0.5);
                }

                // 5. ОБРАБОТКА РЕЗУЛЬТАТОВ ОТСКОКА
                if (!hit)
                {
                    // Если улетели в пустоту (даже если пролетели сквозь мягкий свет) - рисуем фон
                    float bgAlpha;
                    vec3 bgCol = getBackground(currentOrigin, nextDir, bgAlpha, false);
                    accumulatedColor += throughput * bgCol;
                    break; 
                }

                // Если луч сейчас находится внутри объёма,
                // на этом участке пути материал должен окрасить и ослабить его
                if (isInside)
                {
                    float segmentLen = max(tBounce, 0.001);
                    if (u_physGlass == true)
                    {
                        vec3 absorbance = (1.0 - albedo) * 2.0;
                        throughput *= exp(-absorbance * segmentLen);
                    }
                }

                // Обновляем данные для следующей итерации цикла (r++)
                currentOrigin = nextPoint;
                currentDir = nextDir;
                // Нормаль всегда должна смотреть навстречу лучу
                currentNormal = calcNormalGlobal(currentOrigin) * currentSign; 

                persistentConeRadius += tBounce * coneAperture;
                pathAcc += tBounce;

                // Прерываем, если луч потерял энергию
                if (max(throughput.r, max(throughput.g, throughput.b)) < 0.01) break;
            }

            // Добавляем свечение источника
            accumulatedColor += primaryGlow;
            // Вывод цвета с применением ACESFilm
            fragColor = vec4(ACESFilmToneMap(accumulatedColor), 1.0);
            if(!u_outputLinear)
            {
                float dith = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453);
                fragColor.rgb = clamp(fragColor.rgb + (dith - 0.5) * (1.5 / 255.0), 0.0, 1.0);
            }

        }
    }
    else
    {
        float bgAlpha;
        vec3 bgCol = getBackground(rayOrigin, rayDir, bgAlpha, true);
        if (u_transparentBackground == 1)
        {
            // Прозрачный фон: ореол остаётся полупрозрачным —
            // альфа из яркости свечения (max-компонента => rgb <= alpha,
            // корректно и для premultipliedAlpha)
            vec3 glowRGB = ACESFilmToneMap(primaryGlow);
            float glowA = clamp(max(glowRGB.r, max(glowRGB.g, glowRGB.b)), 0.0, 1.0);
            fragColor = vec4(glowRGB, glowA);
        }
        else
        {
            bgCol += primaryGlow;
            fragColor = vec4(ACESFilmToneMap(bgCol), bgAlpha);
        }
        if (!u_outputLinear)
        {
            float dith = fract(sin(dot(gl_FragCoord.xy, vec2(12.9898, 78.233))) * 43758.5453);
            fragColor.rgb = clamp(fragColor.rgb + (dith - 0.5) * (1.5 / 255.0), 0.0, 1.0);
        }

    }

}


// void main() {
//     float w = max(float(u_TBPWidth), 1.0);
//     vec2 uv = vec2(0.5 / w, 0.5);
//     vec4 t = texture(u_TBP, uv);

//     float mc = float(u_materialsCount);
//     float oc = float(u_nodesCount);

//     vec3 color = vec3(
//         clamp(t.x, 0.0, 1.0),
//         clamp(mc / 8.0, 0.0, 1.0),
//         clamp(oc / 8.0, 0.0, 1.0)
//     );

//     fragColor = vec4(color, 1.0);
// }