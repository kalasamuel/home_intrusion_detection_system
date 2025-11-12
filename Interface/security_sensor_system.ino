#define IR_PIN 2        // IR sensor signal (2-pin type: 1→D2, 1→GND)
#define SOUND_PIN 3     // Sound sensor output pin (3-pin type OUT→D3, VCC→5V, GND→GND)
#define LED_PIN 13      // Onboard indicator LED

void setup() {
  pinMode(IR_PIN, INPUT_PULLUP);   // 2-pin IR uses internal pull-up
  pinMode(SOUND_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);

  Serial.begin(9600);
  Serial.println("Security system ready (IR + Sound)");
}

void loop() {
  int ir_state = digitalRead(IR_PIN);       // LOW when beam broken
  int sound_state = digitalRead(SOUND_PIN); // HIGH when loud sound

  bool ir_triggered = (ir_state == LOW);
  bool sound_triggered = (sound_state == HIGH);

  if (ir_triggered && sound_triggered) {
    Serial.println("B");
    digitalWrite(LED_PIN, HIGH);
  } else if (ir_triggered) {
    Serial.println("I");
    digitalWrite(LED_PIN, HIGH);
  } else if (sound_triggered) {
    Serial.println("S");
    digitalWrite(LED_PIN, HIGH);
  } else {
    digitalWrite(LED_PIN, LOW);
  }

  delay(200);  // sample rate
}
