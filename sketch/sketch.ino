#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>
#include <Arduino_RouterBridge.h>
#include <Adafruit_VL53L0X.h>
#include <Adafruit_SSD1306.h>

// Initialize Adafruit PWM Servo Driver at default address 0x40
Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40);

// Initialize VL53L0X Laser Distance Sensor
Adafruit_VL53L0X lox = Adafruit_VL53L0X();

// 128x64 I2C OLED. SCK on the module is the I2C clock/SCL line.
#define OLED_WIDTH 128
#define OLED_HEIGHT 64
#define OLED_RESET -1
#define OLED_ADDRESS 0x3C
Adafruit_SSD1306 oled(OLED_WIDTH, OLED_HEIGHT, &Wire, OLED_RESET);

enum FaceState {
  FACE_IDLE = 0,
  FACE_HAPPY = 1,
  FACE_ALERT = 2,
  FACE_LEFT = 3,
  FACE_RIGHT = 4
};

volatile int requestedFaceState = FACE_IDLE;
int displayedFaceState = -1;
unsigned long lastFaceFrame = 0;
unsigned long lastBlink = 0;
bool faceBlinking = false;

#define SERVO_FREQ 50
#define MPU_ADDR 0x68

struct ServoConfig {
  uint8_t channel;
  uint16_t centerUs;
  int8_t direction; // 1 or -1
  const char *name;
};

// 12 joints config mapping based on measured directions and defaults
ServoConfig servos[] = {
  // FL
  {9,  1500, -1, "FL Foot"},
  {10, 1700,  1, "FL Joint"},
  {11, 1500,  1, "FL Hip"},
  
  // FR
  {13, 1500,  1, "FR Foot"},
  {15, 1300, -1, "FR Joint"},
  {14, 1400, -1, "FR Hip"},
  
  // RL
  {1,  1500,  1, "RL Foot"},
  {2,  1500, -1, "RL Joint"},
  {3,  1600,  1, "RL Hip"},
  
  // RR
  {5,  1500, -1, "RR Foot"},
  {6,  1500,  1, "RR Joint"},
  {12, 1550, -1, "RR Hip"}
};

const int numServos = sizeof(servos) / sizeof(servos[0]);

// Set a specific joint by index with a microsecond offset from its center
void writeJointOffset(int index, int offsetUs) {
  if (index < 0 || index >= numServos) return;
  uint16_t pulseUs = servos[index].centerUs + (offsetUs * servos[index].direction);
  pulseUs = constrain(pulseUs, 600, 2400);
  pwm.writeMicroseconds(servos[index].channel, pulseUs);
}

// Single RPC call to set all 12 servo pulse widths at once (prevents bridge congestion)
int setAllServos(int fl_f, int fl_j, int fl_h, 
                 int fr_f, int fr_j, int fr_h, 
                 int rl_f, int rl_j, int rl_h, 
                 int rr_f, int rr_j, int rr_h) {
  pwm.writeMicroseconds(9,  constrain(fl_f, 600, 2400));
  pwm.writeMicroseconds(10, constrain(fl_j, 600, 2400));
  pwm.writeMicroseconds(11, constrain(fl_h, 600, 2400));

  pwm.writeMicroseconds(13, constrain(fr_f, 600, 2400));
  pwm.writeMicroseconds(15, constrain(fr_j, 600, 2400));
  pwm.writeMicroseconds(14, constrain(fr_h, 600, 2400));

  pwm.writeMicroseconds(1,  constrain(rl_f, 600, 2400));
  pwm.writeMicroseconds(2,  constrain(rl_j, 600, 2400));
  pwm.writeMicroseconds(3,  constrain(rl_h, 600, 2400));

  pwm.writeMicroseconds(5,  constrain(rr_f, 600, 2400));
  pwm.writeMicroseconds(6,  constrain(rr_j, 600, 2400));
  pwm.writeMicroseconds(12, constrain(rr_h, 600, 2400));

  return 0;
}

// Initialize the MPU6050 Accelerometer/Gyroscope
void initMPU() {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x6B); // PWR_MGMT_1 register
  Wire.write(0);    // Wake up MPU6050
  Wire.endTransmission(true);
}

// Reads distance from VL53L0X and raw 6-axis data from MPU6050
// Returns a comma-separated String containing [distance, ax, ay, az, gx, gy, gz]
String readSensors() {
  int distanceMm = -1;
  VL53L0X_RangingMeasurementData_t measure;
  lox.rangingTest(&measure, false);
  if (measure.RangeStatus != 4) { // 4 is phase failure (out of range/invalid)
    distanceMm = measure.RangeMilliMeter;
  }

  // Query MPU6050 raw registers
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(0x3B); // starting with register 0x3B (ACCEL_XOUT_H)
  Wire.endTransmission(false);
  Wire.requestFrom(MPU_ADDR, 14, true);

  int16_t ax = Wire.read() << 8 | Wire.read();
  int16_t ay = Wire.read() << 8 | Wire.read();
  int16_t az = Wire.read() << 8 | Wire.read();
  Wire.read() << 8 | Wire.read(); // Skip temp register (2 bytes)
  int16_t gx = Wire.read() << 8 | Wire.read();
  int16_t gy = Wire.read() << 8 | Wire.read();
  int16_t gz = Wire.read() << 8 | Wire.read();

  // Package as a string: "dist,ax,ay,az,gx,gy,gz"
  return String(distanceMm) + "," +
         String(ax) + "," + String(ay) + "," + String(az) + "," +
         String(gx) + "," + String(gy) + "," + String(gz);
}

// Called through Bridge; only update state here. OLED drawing stays in loop().
int setDisplayState(int state) {
  if (state < FACE_IDLE || state > FACE_RIGHT) return -1;
  requestedFaceState = state;
  return 0;
}

void drawCuteFace(int state, bool blinking) {
  oled.clearDisplay();

  const int leftEyeX = 38;
  const int rightEyeX = 90;
  const int eyeY = 27;
  const int eyeW = 24;
  const int eyeH = 20;

  // Friendly rounded eyes rather than a circular smiley face.
  if (blinking) {
    oled.drawFastHLine(leftEyeX - 10, eyeY, 20, SSD1306_WHITE);
    oled.drawFastHLine(rightEyeX - 10, eyeY, 20, SSD1306_WHITE);
  } else {
    oled.fillRoundRect(leftEyeX - eyeW / 2, eyeY - eyeH / 2,
                       eyeW, eyeH, 7, SSD1306_WHITE);
    oled.fillRoundRect(rightEyeX - eyeW / 2, eyeY - eyeH / 2,
                       eyeW, eyeH, 7, SSD1306_WHITE);

    int pupilOffset = 0;
    if (state == FACE_LEFT) pupilOffset = -5;
    if (state == FACE_RIGHT) pupilOffset = 5;
    oled.fillCircle(leftEyeX + pupilOffset, eyeY, 5, SSD1306_BLACK);
    oled.fillCircle(rightEyeX + pupilOffset, eyeY, 5, SSD1306_BLACK);
  }

  if (state == FACE_ALERT) {
    oled.drawTriangle(64, 43, 57, 55, 71, 55, SSD1306_WHITE);
    oled.drawFastVLine(64, 47, 5, SSD1306_BLACK);
    oled.drawPixel(64, 54, SSD1306_BLACK);
  } else if (state == FACE_HAPPY) {
    oled.drawLine(54, 47, 59, 51, SSD1306_WHITE);
    oled.drawLine(59, 51, 64, 53, SSD1306_WHITE);
    oled.drawLine(64, 53, 69, 51, SSD1306_WHITE);
    oled.drawLine(69, 51, 74, 47, SSD1306_WHITE);
  } else {
    oled.drawFastHLine(57, 50, 14, SSD1306_WHITE);
  }

  oled.display();
}

void updateCuteFace() {
  unsigned long now = millis();

  // Blink briefly every few seconds.
  if (now - lastBlink >= 3200) {
    lastBlink = now;
    faceBlinking = true;
  }
  if (faceBlinking && now - lastBlink >= 140) {
    faceBlinking = false;
  }

  if (displayedFaceState != requestedFaceState ||
      faceBlinking || now - lastFaceFrame >= 100) {
    displayedFaceState = requestedFaceState;
    lastFaceFrame = now;
    drawCuteFace(displayedFaceState, faceBlinking);
  }
}

void setStandingPose() {
  int hipOffset = 0;       // Hips pointing straight
  int jointOffset = -350;  // Middle joints move UP (negative offset)
  int footOffset = 350;    // Feet move INWARDS/DOWN (positive offset)

  for (int i = 0; i < numServos; i++) {
    const char *name = servos[i].name;
    int offset = 0;
    
    if (strstr(name, "Hip") != NULL) {
      offset = hipOffset;
    } else if (strstr(name, "Joint") != NULL) {
      offset = jointOffset;
    } else if (strstr(name, "Foot") != NULL) {
      offset = footOffset;
    }
    
    writeJointOffset(i, offset);
  }
}

void setup() {
  // Initialize communication bridge
  Bridge.begin();
  Bridge.provide("setAllServos", setAllServos);
  Bridge.provide("readSensors", readSensors);
  Bridge.provide("setDisplayState", setDisplayState);

  // Initialize I2C Bus and MPU6050
  Wire.begin();

  bool oledReady = oled.begin(SSD1306_SWITCHCAPVCC, OLED_ADDRESS);
  if (!oledReady) {
    // Some modules use 0x3D instead of the usual 0x3C.
    oledReady = oled.begin(SSD1306_SWITCHCAPVCC, 0x3D);
  }
  if (oledReady) {
    oled.clearDisplay();
    oled.display();
    drawCuteFace(FACE_IDLE, false);
  }
  initMPU();

  // Initialize VL53L0X
  lox.begin();

  pwm.begin();
  pwm.setOscillatorFrequency(27000000);
  pwm.setPWMFreq(SERVO_FREQ);
  delay(500);

  setStandingPose();
}

void loop() {
  updateCuteFace();
  delay(10);
}
