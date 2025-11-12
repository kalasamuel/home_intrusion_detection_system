#ifndef F_CPU
#define F_CPU 16000000UL
#endif

#include <avr/io.h>
#include <util/delay.h>
#include <stdint.h>

/* ---------------- SOUND SENSOR SECTION ---------------- */
#define SOUND_ADC_CHANNEL 2
#define RED_LED PB3
#define SOUND_THRESHOLD 60

void ADC_Init(void);
uint16_t ADC_Read(uint8_t channel);

/* ---------------- IR SENSOR SECTION ---------------- */
#define IR_TX_PIN      PB0
#define YELLOW_LED     PB1
#define IR_RX_PIN      PD2

/* --------------- UART COMMUNICATION ----------------- */
#define USART_BAUDRATE 9600
#define BAUD_PRESCALE ((F_CPU/16UL)/USART_BAUDRATE - 1UL)

void UART_init(unsigned int ubrr);
void UART_TxChar(unsigned char ch);

void UART_init(unsigned int ubrr)
{
	UBRR0H = (unsigned char)(ubrr >> 8);
	UBRR0L = (unsigned char)ubrr;
	UCSR0B = (1 << TXEN0);
	UCSR0C = (1 << UCSZ01) | (1 << UCSZ00);
}

void UART_TxChar(unsigned char ch)
{
	while (!(UCSR0A & (1 << UDRE0)));
	UDR0 = ch;
}

int main(void)
{
    DDRB |= (1 << IR_TX_PIN) | (1 << YELLOW_LED);
    DDRD &= ~(1 << IR_RX_PIN);
    PORTD |= (1 << IR_RX_PIN);
    PORTB |= (1 << IR_TX_PIN);

    ADC_Init();
	UART_init((unsigned int)BAUD_PRESCALE);

    DDRB |= (1 << RED_LED);

    uint16_t soundValue;

    while (1)
    {
		uint8_t beam_blocked = PIND & (1 << IR_RX_PIN);
		if (beam_blocked) {
			for (uint8_t i=0; i < 5; i++)
			{
				PORTB ^= (1 << RED_LED); 
				_delay_ms(100);
			}
			UART_TxChar('I');
		} else {
			PORTB &= ~(1 << YELLOW_LED);
		}

		soundValue = (ADC_Read(SOUND_ADC_CHANNEL) * 202UL) / 100UL;
		if (soundValue > SOUND_THRESHOLD) {
			for (uint8_t i=0; i<5; i++)
			{
				PORTB ^= (1 << YELLOW_LED);
				_delay_ms(100);
			}
			UART_TxChar('S');
		} else {
			PORTB &= ~(1 << RED_LED);
		}

        if (beam_blocked && (soundValue > SOUND_THRESHOLD))
        {
            for (uint8_t i = 0; i < 100; i++)
            {
                PORTB |= (1 << YELLOW_LED);
                PORTB &= ~(1 << RED_LED);
                _delay_ms(25);

                PORTB |= (1 << RED_LED);
                PORTB &= ~(1 << YELLOW_LED);
                _delay_ms(25);
            }

            PORTB &= ~((1 << RED_LED) | (1 << YELLOW_LED));
			UART_TxChar('B');
        }

        _delay_ms(10);
    }

    return 0;
}

/* ---------------- SOUND SENSOR FUNCTIONS ---------------- */
void ADC_Init(void)
{
    ADMUX = (1 << REFS0);
    ADCSRA = (1 << ADEN)
           | (1 << ADPS2) | (1 << ADPS1) | (1 << ADPS0);
}

uint16_t ADC_Read(uint8_t channel)
{
    ADMUX = (ADMUX & 0xF0) | (channel & 0x0F);
    _delay_us(5);
    ADCSRA |= (1 << ADSC);
    while (ADCSRA & (1 << ADSC));
    return ADC;
}
