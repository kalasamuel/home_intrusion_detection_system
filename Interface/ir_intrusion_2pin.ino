#define IR_PIN 2          // The data pin connected to the IR receiver (second pin to GND)
#define LED_PIN 13        // Optional onboard LED indicator

void setup() {
  pinMode(IR_PIN, INPUT_PULLUP); // Use Arduino's internal pull-up resistor
  pinMode(LED_PIN, OUTPUT);
  
  Serial.begin(9600);
  Serial.println("IR Sensor Initialized (2-pin mode)");
}

void loop() {
  int ir_state = digitalRead(IR_PIN);

  // When IR beam is broken / object detected
  if (ir_state == LOW) {  
    digitalWrite(LED_PIN, HIGH);  // Turn on LED for visual alert
    Serial.println("IR_TRIGGER"); // Send signal to Python
    delay(2000);                  // Debounce delay to prevent flooding
  } 
  else {
    digitalWrite(LED_PIN, LOW);
  }

  delay(100);
}
