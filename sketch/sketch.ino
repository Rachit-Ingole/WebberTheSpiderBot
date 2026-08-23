#include <Wire.h>
#include <math.h>
#include <string.h>
#include <Adafruit_PWMServoDriver.h>
#include <Arduino_RouterBridge.h>
#include <Adafruit_VL53L0X.h>
#include <zephyr/kernel.h>
#include "font5x7.h"

#define OLED_WIDTH 128
#define OLED_HEIGHT 64
#define OLED_ADDRESS 0x3C
#define OLED_ENABLED 1
#define SENSORS_ENABLED 1

void drawChar(int x, int y, char c, bool color = true);

volatile unsigned long servoWriteCount = 0;
bool setupComplete = false;
K_MUTEX_DEFINE(i2cMutex);

// Direct I2C driver avoids the SSD1306 library's incompatible Zephyr GPIO macros.
class SimpleOLED {
 public:
  uint8_t buffer[OLED_WIDTH * OLED_HEIGHT / 8];
  uint8_t address;

  bool begin(uint8_t i2cAddress) {
    address = i2cAddress;
    const uint8_t commands[] = {
      0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40,
      0x8D, 0x14, 0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12,
      0x81, 0x8F, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF
    };
    Wire.beginTransmission(address);
    Wire.write(0x00);
    for (uint8_t command : commands) Wire.write(command);
    if (Wire.endTransmission() != 0) return false;
    clearDisplay();
    display();
    return true;
  }

  void clearDisplay() { memset(buffer, 0, sizeof(buffer)); }

  void drawPixel(int x, int y, bool color = true) {
    if (x < 0 || x >= OLED_WIDTH || y < 0 || y >= OLED_HEIGHT) return;
    uint16_t index = x + (y / 8) * OLED_WIDTH;
    if (color) buffer[index] |= (1 << (y & 7));
    else buffer[index] &= ~(1 << (y & 7));
  }

  void drawFastHLine(int x, int y, int width, bool color = true) {
    for (int i = 0; i < width; i++) drawPixel(x + i, y, color);
  }

  void drawFastVLine(int x, int y, int height, bool color = true) {
    for (int i = 0; i < height; i++) drawPixel(x, y + i, color);
  }

  void fillRect(int x, int y, int width, int height, bool color = true) {
    for (int yy = y; yy < y + height; yy++) drawFastHLine(x, yy, width, color);
  }

  void fillRoundRect(int x, int y, int width, int height, int radius, bool color = true) {
    fillRect(x + radius, y, width - 2 * radius, height, color);
    fillRect(x, y + radius, width, height - 2 * radius, color);
    fillCircle(x + radius, y + radius, radius, color);
    fillCircle(x + width - radius - 1, y + radius, radius, color);
    fillCircle(x + radius, y + height - radius - 1, radius, color);
    fillCircle(x + width - radius - 1, y + height - radius - 1, radius, color);
  }

  void fillCircle(int cx, int cy, int radius, bool color = true) {
    for (int y = -radius; y <= radius; y++) {
      int half = (int)sqrt(radius * radius - y * y);
      drawFastHLine(cx - half, cy + y, half * 2 + 1, color);
    }
  }

  void drawLine(int x0, int y0, int x1, int y1, bool color = true) {
    int dx = abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
    int dy = -abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
    int error = dx + dy;
    while (true) {
      drawPixel(x0, y0, color);
      if (x0 == x1 && y0 == y1) break;
      int e2 = 2 * error;
      if (e2 >= dy) { error += dy; x0 += sx; }
      if (e2 <= dx) { error += dx; y0 += sy; }
    }
  }

  void drawTriangle(int x0, int y0, int x1, int y1, int x2, int y2, bool color = true) {
    drawLine(x0, y0, x1, y1, color);
    drawLine(x1, y1, x2, y2, color);
    drawLine(x2, y2, x0, y0, color);
  }

  void display() {
    k_mutex_lock(&i2cMutex, K_FOREVER);
    for (uint8_t page = 0; page < 8; page++) {
      Wire.beginTransmission(address);
      Wire.write(0x00);
      Wire.write(0xB0 | page);
      Wire.write(0x00);
      Wire.write(0x10);
      Wire.endTransmission();
      for (uint8_t offset = 0; offset < OLED_WIDTH; offset += 16) {
        Wire.beginTransmission(address);
        Wire.write(0x40);
        for (uint8_t i = 0; i < 16; i++) Wire.write(buffer[page * OLED_WIDTH + offset + i]);
        Wire.endTransmission();
      }
    }
    k_mutex_unlock(&i2cMutex);
  }
};

// Initialize Adafruit PWM Servo Driver at default address 0x40
Adafruit_PWMServoDriver pwm = Adafruit_PWMServoDriver(0x40);

// Initialize VL53L0X Laser Distance Sensor
Adafruit_VL53L0X lox = Adafruit_VL53L0X();

// 128x64 I2C OLED. SCK on the module is the I2C clock/SCL line.
SimpleOLED oled;

#define SSD1306_WHITE true
#define SSD1306_BLACK false

enum FaceState {
  FACE_IDLE = 0,
  FACE_HAPPY = 1,
  FACE_ALERT = 2,
  FACE_LEFT = 3,
  FACE_RIGHT = 4,
  FACE_TEXT = 5,
  FACE_SLEEP = 6
};

volatile int requestedFaceState = FACE_IDLE;
int displayedFaceState = -1;
bool displayedFaceBlinking = false;
unsigned long lastBlink = 0;
bool faceBlinking = false;
unsigned long lastFaceFrame = 0;
String scrollText = "";
int scrollX = OLED_WIDTH;

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
  k_mutex_lock(&i2cMutex, K_FOREVER);
  servoWriteCount++;
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

  k_mutex_unlock(&i2cMutex);
  return 0;
}

String getDiagnostics() {
  k_mutex_lock(&i2cMutex, K_FOREVER);
  Wire.beginTransmission(0x40);
  bool pcaAck = (Wire.endTransmission() == 0);
  k_mutex_unlock(&i2cMutex);
  return String("setup=") + (setupComplete ? "complete" : "incomplete") +
         ",servo_writes=" + String(servoWriteCount) +
         ",pca=" + String(pcaAck ? "ack" : "missing") +
         ",oled=" + String(OLED_ENABLED ? "enabled" : "disabled") +
         ",sensors=" + String(SENSORS_ENABLED ? "enabled" : "disabled");
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
  if (!SENSORS_ENABLED) return "-1,0,0,0,0,0,0";
  k_mutex_lock(&i2cMutex, K_FOREVER);
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
  int16_t raw_temp = Wire.read() << 8 | Wire.read(); // Read MPU6050 temperature
  int16_t gx = Wire.read() << 8 | Wire.read();
  int16_t gy = Wire.read() << 8 | Wire.read();
  int16_t gz = Wire.read() << 8 | Wire.read();

  // Package as a string: "dist,ax,ay,az,gx,gy,gz,raw_temp"
  String result = String(distanceMm) + "," +
         String(ax) + "," + String(ay) + "," + String(az) + "," +
         String(gx) + "," + String(gy) + "," + String(gz) + "," + String(raw_temp);
  k_mutex_unlock(&i2cMutex);
  return result;
}

// Called through Bridge; only update state here. OLED drawing stays in loop().
int setDisplayState(int state) {
  if (state < FACE_IDLE || state > FACE_SLEEP) return -1;
  requestedFaceState = state;
  return 0;
}

void drawCuteFace(int state, bool blinking) {
  oled.clearDisplay();

  const int leftEyeX = 32;
  const int rightEyeX = 96;
  const int eyeY = 32; // Centered vertically on 64px display

  if (state == FACE_HAPPY) {
    // Happy eyes: Cute upward-curving arches / domes!
    oled.fillCircle(leftEyeX, eyeY + 8, 22, SSD1306_WHITE);
    oled.fillRect(leftEyeX - 22, eyeY + 8, 44, 22, SSD1306_BLACK);
    oled.fillCircle(rightEyeX, eyeY + 8, 22, SSD1306_WHITE);
    oled.fillRect(rightEyeX - 22, eyeY + 8, 44, 22, SSD1306_BLACK);
  } 
  else if (state == FACE_SLEEP) {
    // Sleepy eyes: Thick horizontal closed bars near the bottom.
    oled.fillRoundRect(leftEyeX - 20, eyeY + 6, 40, 10, 5, SSD1306_WHITE);
    oled.fillRoundRect(rightEyeX - 20, eyeY + 6, 40, 10, 5, SSD1306_WHITE);
  }
  else if (blinking) {
    // Blinking: Thin closed slits.
    oled.fillRoundRect(leftEyeX - 20, eyeY + 6, 40, 4, 1, SSD1306_WHITE);
    oled.fillRoundRect(rightEyeX - 20, eyeY + 6, 40, 4, 1, SSD1306_WHITE);
  } 
  else if (state == FACE_ALERT) {
    // Alert/Surprised: Big round white circles with tiny black pupils in the middle!
    oled.fillCircle(leftEyeX, eyeY, 26, SSD1306_WHITE);
    oled.fillCircle(leftEyeX, eyeY, 6, SSD1306_BLACK);
    oled.fillCircle(rightEyeX, eyeY, 26, SSD1306_WHITE);
    oled.fillCircle(rightEyeX, eyeY, 6, SSD1306_BLACK);
  } 
  else {
    // FACE_IDLE, FACE_LEFT, FACE_RIGHT:
    // Big rounded rectangles for the eyes, with moving pupils.
    const int eyeW = 42;
    const int eyeH = 38;
    const int eyeRadius = 13;
    
    oled.fillRoundRect(leftEyeX - eyeW / 2, eyeY - eyeH / 2, eyeW, eyeH, eyeRadius, SSD1306_WHITE);
    oled.fillRoundRect(rightEyeX - eyeW / 2, eyeY - eyeH / 2, eyeW, eyeH, eyeRadius, SSD1306_WHITE);

    int pupilOffset = 0;
    if (state == FACE_LEFT) pupilOffset = -8;
    if (state == FACE_RIGHT) pupilOffset = 8;
    
    oled.fillCircle(leftEyeX + pupilOffset, eyeY, 10, SSD1306_BLACK);
    oled.fillCircle(rightEyeX + pupilOffset, eyeY, 10, SSD1306_BLACK);
  }

  oled.display();
}

void updateCuteFace() {
  unsigned long now = millis();

  if (requestedFaceState == FACE_TEXT) {
    if (now - lastFaceFrame >= 30) { // 30ms scroll speed step
      lastFaceFrame = now;
      oled.clearDisplay();
      int x = scrollX;
      for (unsigned int i = 0; i < scrollText.length(); i++) {
        char c = scrollText[i];
        if (x + 10 >= 0 && x < OLED_WIDTH) { // clipping check
          drawChar(x, 24, c, true); // Centered vertically (font height is 14px, centered in 64px is (64-14)/2 = 25, let's use 24)
        }
        x += 12; // 10px width + 2px spacing
      }
      oled.display();
      scrollX -= 3; // Scroll slightly faster since font is bigger
      
      int textWidth = scrollText.length() * 12;
      if (scrollX < -textWidth) {
        requestedFaceState = FACE_IDLE;
        displayedFaceState = -1; // Force eye redraw
      }
    }
    return;
  }

  // Blink briefly every few seconds.
  if (now - lastBlink >= 3200) {
    lastBlink = now;
    faceBlinking = true;
  }
  if (faceBlinking && now - lastBlink >= 140) {
    faceBlinking = false;
  }

  if (displayedFaceState != requestedFaceState ||
      displayedFaceBlinking != faceBlinking) {
    displayedFaceState = requestedFaceState;
    displayedFaceBlinking = faceBlinking;
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
void drawChar(int x, int y, char c, bool color) {
  if (c < 0x20 || c > 0x7E) return; // Only printable ASCII
  uint16_t fontIndex = c - 0x20;
  for (int i = 0; i < 5; i++) {
    uint8_t line = pgm_read_byte(&(font5x7[fontIndex][i]));
    for (int j = 0; j < 7; j++) {
      if (line & (1 << j)) {
        oled.fillRect(x + i * 2, y + j * 2, 2, 2, color);
      }
    }
  }
}

int showScrollingText(String text) {
  scrollText = text;
  scrollX = OLED_WIDTH;
  requestedFaceState = FACE_TEXT;
  return 0;
}

void setup() {
  // Initialize communication bridge
  Bridge.begin();
  Bridge.provide("setAllServos", setAllServos);
  Bridge.provide("readSensors", readSensors);
  Bridge.provide("setDisplayState", setDisplayState);
  Bridge.provide("getDiagnostics", getDiagnostics);
  Bridge.provide("showScrollingText", showScrollingText);

  // Initialize I2C Bus and MPU6050
  Wire.begin();

  // Bring up the servo driver and standing pose first. This keeps locomotion
  // available even if an optional OLED or distance sensor is disconnected.
  pwm.begin();
  pwm.setOscillatorFrequency(27000000);
  pwm.setPWMFreq(SERVO_FREQ);
  delay(500);
  setStandingPose();

  if (OLED_ENABLED) {
    bool oledReady = oled.begin(OLED_ADDRESS);
    if (!oledReady) {
      // Some modules use 0x3D instead of the usual 0x3C.
      oledReady = oled.begin(0x3D);
    }
    if (oledReady) {
      oled.clearDisplay();
      oled.display();
      drawCuteFace(FACE_IDLE, false);
    }
  }
  if (SENSORS_ENABLED) {
    initMPU();
    // Initialize VL53L0X
    lox.begin();
  }

  setupComplete = true;

}

void loop() {
  if (OLED_ENABLED) updateCuteFace();
  delay(10);
}
