/*
    Teensy 4.1 firmware for TPT (Triple Pulse Test) application
    Requires StateMachine library, installed via the Arduino
    Library Manager.

    Register definitions in Arduino/hardware/teensy/avr/cores/teensy4/imxrt.h
*/

#include <avr/io.h>
#include <avr/interrupt.h>
#include <StateMachine.h>

StateMachine machine = StateMachine();

State* S0 = machine.addState(&state0); // Init
State* S1 = machine.addState(&state1);  // Serial Listener
State* S2 = machine.addState(&state2); // Store Parameter
State* S3 = machine.addState(&state3); // Triple Pulse Test
State* S4 = machine.addState(&state4); // Inductance Test
State* S5 = machine.addState(&state5); // Set Discharge Resistor
State* S6 = machine.addState(&state6); // Demagnetize Probe
State* S7 = machine.addState(&state7); // Read Memory
State* S8 = machine.addState(&state8); // Reset
State* S9 = machine.addState(&state9); // Demagnetize Core

// Commands and state management
char received_command = '0';
long received_parameter = 0;
bool eval;
bool new_state = 0; // If equal to 1, it means that the program flow
                    // is coming from a different state
                    // Used to execute code only once upon reaching
                    // a new state.

const int ledPin = 13;
const int LED0 = 22;
const int LED1 = 23;
const int LED2 = 20;
const int LED3 = 21;
const int LED4 = 38;
const int LED5 = 39;
const int LED6 = 9;
const int LED7 = 10;
int myPins[] = {LED0, LED1, LED2, LED3, LED4, LED5, LED6, LED7};

// Definition of global variables - In comments the default for 100 kHz, no DC bias
unsigned long T1 = 1500;  //5000 ns
unsigned long T2A = 500; //5000 ns
unsigned long T2B = 500; //5000 ns
unsigned long T3 = 1000; //0 ns
unsigned long TL = 1000;  //5000 ns
volatile byte N = 3; //2 ns
volatile bool CORE_DEMAG_ENABLED = false;
volatile bool CORE_DEMAG_SCHEDULED = false; // required to be true in order to begin the core demagnetization
bool S = false; //false

const unsigned long DT = 80;  //dead-time in nanoseconds

//PWM Variables
volatile byte current_n;
unsigned short ST1_VAL[6];  //VAL0, VAL1, VAL2, VAL3, VAL4, VAL5
unsigned short ST2_VAL[6];  //VAL0, VAL1, VAL2, VAL3, VAL4, VAL5
unsigned short ST3_VAL[6];  //VAL0, VAL1, VAL2, VAL3, VAL4, VAL5
unsigned short IND_VAL[6];

// Global variables OK: true if they were stored properly
bool T1_OK = false;
bool T2A_OK = false;
bool T2B_OK = false;
bool T3_OK = false;
bool TL_OK = false;
bool N_OK = false;
bool S_OK = false;
bool CORE_DEMAG_SCHEDULED_OK = false;

// Register 

// Auxiliary variables (loops, serial...)
char p; // Used to peek the Serial port

void setup() {
  // initialize the digital pin as an output.
  for (char i = 0; i <= 7; i++)
    pinMode(myPins[i], OUTPUT);

  //Initialize Serial port
  Serial.begin(9600);
  delayMicroseconds(10);              //Delay for initialization

  //Define transitions
  S0->addTransition(&transitionS0S1, S1);
  S1->addTransition(&transitionS1S2, S2);
  S1->addTransition(&transitionS1S3, S3);
  S1->addTransition(&transitionS1S4, S4);
  S1->addTransition(&transitionS1S5, S5);
  S1->addTransition(&transitionS1S6, S6);
  S1->addTransition(&transitionS1S7, S7);
  S1->addTransition(&transitionS1S8, S8);
  S1->addTransition(&transitionS1S9, S9);
  S2->addTransition(&transitionS2S1, S1);
  S3->addTransition(&transitionS3S1, S1);
  S4->addTransition(&transitionS4S1, S1);
  S5->addTransition(&transitionS5S1, S1);
  S6->addTransition(&transitionS6S1, S1);
  S7->addTransition(&transitionS7S1, S1);
  S9->addTransition(&transitionS9S1, S1);
}

void loop() {
  machine.run();
}

// ======= ADDITIONAL FUNCTIONS ========== //

void enableLED(char j) {
  for (char i = 0; i <= 7; i++) {
    digitalWrite(myPins[i], LOW);
  }
  digitalWrite(myPins[j], HIGH);
}

void configurePinsFLEXPWM(){
  IOMUXC_SW_MUX_CTL_PAD_GPIO_EMC_06 = 0x00000001;
  IOMUXC_SW_PAD_CTL_PAD_GPIO_EMC_06 = 0x000030B0;
  IOMUXC_SW_MUX_CTL_PAD_GPIO_EMC_07 = 0x00000001;
  IOMUXC_SW_PAD_CTL_PAD_GPIO_EMC_07 = 0x000030B0;
  //这里是否说明是开启了EMC_06与EMC_07,对应在Teensy4.1上就是Pin4,33
}

void initFLEXPWM(){
  // If the fractional delays are off, then the upper 5 bits of DTCNT0 are ignored and the
  //remaining 11 bits are used to specify the number of cycles of deadtime.
  unsigned long F_BUS = F_BUS_ACTUAL; //in Hertz
  double T_BUS_ns = ((double)1000000000) / ((double)F_BUS);
  unsigned short deadtime_cycles = ceil((unsigned short)((double)DT / T_BUS_ns));
  // Verify - should be 12 F_BUS cycles to achieve 80 ns deadtime.
  // DTCNT0 field - deadtime during 0 to 1 transitions of the PWM_A output (normal polarity)
  // DTCNT1 field - deadtime during 0 to 1 transitions of the PWM_B output (normal polarity)
  FLEXPWM2_SM0DTCNT0 = deadtime_cycles; 
  FLEXPWM2_SM0DTCNT1 = deadtime_cycles; 
  
  FLEXPWM2_OUTEN = 0x0110;  // Enable PWM_A and PWM_B outputs.
  FLEXPWM2_MCTRL = 0x0100;  // WARNING, maybe 0x0101 is required to load
                            // new VAL values after each update... TBC. 0x0100                   
  //FLEXPWM2_OUTEN = 0x0110;  // Enable PWM_A and PWM_B outputs.
  FLEXPWM2_FTST0 = 0;
  FLEXPWM2_SM0STS |= FLEXPWM_SMSTS_RF;  //Reload flag: clear                    
  FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //Required ???
  delayMicroseconds(1);
  FLEXPWM2_MCTRL = 0x0101;
}

void stopFLEXPWM(){
  FLEXPWM2_FTST0 |= 1;  // Inject a fault so that the output circuitry turns off.
  FLEXPWM2_MCTRL = 0x0000;  // Stop the counter
  FLEXPWM2_OUTEN = 0x0000;  // Disable PWM_A and PWM_B outputs.
  FLEXPWM2_SM0INTEN = 0; // Disable interrupts
}

FASTRUN void isr_FLEXPWM2_PWM_RELOAD0_TPT(){
  //__disable_irq();
  if(FLEXPWM2_SM0STS & FLEXPWM_SMSTS_RF){ //If RELOAD FLAG IS SET
    FLEXPWM2_SM0STS |= FLEXPWM_SMSTS_RF; //Clear flag by writing a logic 1 to it (0x1000)
    //do things
    current_n++;
    if(current_n > N){
      stopFLEXPWM();
    }
    else if(current_n == 1){
      //Serial.println(FLEXPWM2_SM0VAL2,DEC);
      //To check what was the last value
      //FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
      FLEXPWM2_SM0VAL0 = ST2_VAL[0];
      FLEXPWM2_SM0VAL1 = ST2_VAL[1];
      FLEXPWM2_SM0VAL2 = ST2_VAL[2];
      FLEXPWM2_SM0VAL3 = ST2_VAL[3];
      FLEXPWM2_SM0VAL4 = ST2_VAL[4];
      FLEXPWM2_SM0VAL5 = ST2_VAL[5];
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values (Stage III+IV)
    }
    else if(current_n == N-1){ //N-1 because values must be loaded beforehand
      //FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
      FLEXPWM2_SM0VAL0 = ST3_VAL[0];
      FLEXPWM2_SM0VAL1 = ST3_VAL[1];
      FLEXPWM2_SM0VAL2 = ST3_VAL[2];
      FLEXPWM2_SM0VAL3 = ST3_VAL[3];
      FLEXPWM2_SM0VAL4 = ST3_VAL[4];
      FLEXPWM2_SM0VAL5 = ST3_VAL[5];   
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values (Stage III+IV)
    }else if(current_n == N){
      //TO AVOID SPIKES BETWEEN DISABILITATION OF THE PWM MODULE AND THE EXECUTION OF THE ISR,
      //DISABLE THE UNUSED CHANNEL ONCE IT GOES DOWN
      if(!S){
        FLEXPWM2_SM0INTEN |= FLEXPWM_SMSTS_CMPF(3); // ALSO ENABLE INTERRUPT ON COMPARE VAL 3
      }else{
        FLEXPWM2_SM0INTEN |= FLEXPWM_SMSTS_CMPF(5); // ALSO ENABLE INTERRUPT ON COMPARE VAL 5
      }
    }
    FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15);
    //__enable_irq();
  }else{  //INTERRUPT COMPARE VAL3 or VAL5
    //Clear flag(s)
    FLEXPWM2_SM0STS |= (FLEXPWM_SMSTS_CMPF(3) | FLEXPWM_SMSTS_CMPF(5));
    if(!S){ //Positive
      Serial.println(current_n,DEC);
      FLEXPWM2_OUTEN = 0x0010;  //Disable PWMA
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
      FLEXPWM2_SM0VAL5 = 0;
      FLEXPWM2_SM0VAL3 = 0;
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values (Stage III+IV)
    }else{
      //Make sure that 
      FLEXPWM2_OUTEN = 0x0100; //Disable PWMB
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
      FLEXPWM2_SM0VAL5 = 0;
      FLEXPWM2_SM0VAL3 = 0;
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values (Stage III+IV)
    }
  }
}

void calculateRegistersFLEXPWM_TPT(char i_PS){
  //For a given prescaler and TPT parameters, fill in ST1_VAL, ST2_VAL and ST3_VAL
  unsigned long F_BUS = F_BUS_ACTUAL; //in Hertz
  //Calculate the PWM clock period resolution for each prescaler
  //That is, how many nanoseconds lasts a prescaled PWM clock period
  double counter_clock_resolution_ns[8];
  for(char i = 0; i < 8; i++){
    counter_clock_resolution_ns[i] = ((double)1000000000 * (double)(1<<i)) / ((double)F_BUS);
  }
  double t_ns = counter_clock_resolution_ns[i_PS]; // HAY QIE HACERLO EN i, no en prescaler
  Serial.print("t_ns: "); Serial.print(t_ns, DEC); Serial.println(" ns");
  if(S == 0){ //Positive (begin with PWMA)
    ST1_VAL[0] = (unsigned short)((double)(T1 + T2B) / (2 * t_ns));
    ST1_VAL[1] = (unsigned short)((double)(T1 + T2B) / t_ns);
    ST1_VAL[2] = (unsigned short)((double)(DT) / t_ns);
    ST1_VAL[3] = (unsigned short)((double)(T1) / t_ns);
    ST1_VAL[4] = (unsigned short)((double)(T1 + DT) / t_ns);
    ST1_VAL[5] = ST1_VAL[1];
  
    ST2_VAL[0] = (unsigned short)((double)(T2A + T2B) / (2 * t_ns));
    ST2_VAL[1] = (unsigned short)((double)(T2A + T2B) / t_ns);
    ST2_VAL[2] = (unsigned short)((double)(DT) / t_ns);
    ST2_VAL[3] = (unsigned short)((double)(T2A) / t_ns);
    ST2_VAL[4] = (unsigned short)((double)(T2A + DT) / t_ns);
    ST2_VAL[5] = ST2_VAL[1];
  
    ST3_VAL[0] = (unsigned short)((double)(T2A + T3) / (2 * t_ns));
    ST3_VAL[1] = (unsigned short)((double)(T2A + T3) / t_ns);
    //ST3_VAL[1] = 0xFFFF;
    ST3_VAL[2] = (unsigned short)((double)(DT) / t_ns);
    ST3_VAL[3] = (unsigned short)((double)(T2A) / t_ns);
    ST3_VAL[4] = (unsigned short)((double)(T2A + DT) / t_ns);
    ST3_VAL[5] = ST3_VAL[1];
    ST3_VAL[1]++;
    
  }else{  //Negative (begin with PWMB)
    ST1_VAL[0] = (unsigned short)((double)(T1 + T2B) / (2 * t_ns));
    ST1_VAL[1] = (unsigned short)((double)(T1 + T2B) / t_ns);
    ST1_VAL[2] = (unsigned short)((double)(T1 + DT) / t_ns);
    ST1_VAL[3] = ST1_VAL[1];
    ST1_VAL[4] = (unsigned short)((double)(DT) / t_ns);
    ST1_VAL[5] = (unsigned short)((double)(T1) / t_ns);
    
    ST2_VAL[0] = (unsigned short)((double)(T2A + T2B) / (2 * t_ns));
    ST2_VAL[1] = (unsigned short)((double)(T2A + T2B) / t_ns);
    ST2_VAL[2] = (unsigned short)((double)(T2A + DT) / t_ns);
    ST2_VAL[3] = ST2_VAL[1];
    ST2_VAL[4] = (unsigned short)((double)(DT) / t_ns);
    ST2_VAL[5] = (unsigned short)((double)(T2A) / t_ns);

    ST3_VAL[0] = (unsigned short)((double)(T2A + T3) / (2 * t_ns)); //OK
    ST3_VAL[1] = (unsigned short)((double)(T2A + T3) / t_ns); //OK
    ST3_VAL[4] = (unsigned short)((double)(DT) / t_ns);
    ST3_VAL[3] = ST3_VAL[1];
    ST3_VAL[5] = (unsigned short)((double)(T2A) / t_ns);
    ST3_VAL[2] = (unsigned short)((double)(T2A + DT) / t_ns);
    ST3_VAL[1]++;
  }
    Serial.println("-- STAGE I --");
    for(char i = 0; i < 6; i++){
      Serial.print("VAL");  Serial.print(i,DEC);  Serial.print(": "); Serial.println(ST1_VAL[i], DEC);
    }
    Serial.println("-- STAGE II --");
    for(char i = 0; i < 6; i++){
      Serial.print("VAL");  Serial.print(i,DEC);  Serial.print(": "); Serial.println(ST2_VAL[i], DEC);
    }
    Serial.println("-- STAGES III & IV --");
    for(char i = 0; i < 6; i++){
      Serial.print("VAL");  Serial.print(i,DEC);  Serial.print(": "); Serial.println(ST3_VAL[i], DEC);
    }
}

void configureFLEXPWM_TPT(){
  //Register names defined in imxrt.h
  //Find out which is the longest stage
  unsigned long duration_Stage_I = T1 + T2B;
  unsigned long duration_Stage_II = T2A + T2B;
  unsigned long duration_Stage_III_IV = T2A + T3;
  unsigned long max_duration = max(max(duration_Stage_I, duration_Stage_II), duration_Stage_III_IV); //ns
  //Pick the smallest prescaler that allows holding the longest stage
  char prescalers[] = {1, 2, 4, 8, 16, 32, 64, 128};
  char prescaler;
  bool error_no_prescaler = true; //In case a stage is so long that can't fit
  unsigned long max_counter_times[] = {436900, 873800, 1747600, 3495200, 6990400,
                                       13980800, 27961600, 55923200};

  char i_PS;
  for(i_PS = 0; i_PS <= 7; i_PS++){
    if(max_duration <= max_counter_times[i_PS]){
      prescaler = prescalers[i_PS];
      error_no_prescaler = false;
      break;
    }
  }
  if(!error_no_prescaler){
    FLEXPWM2_SM0CTRL = ((uint16_t)(1<<10)) | (((uint16_t)i_PS)<<4); // Prescaler
    FLEXPWM2_SM0CTRL2 = FLEXPWM_SMCTRL2_INDEP | FLEXPWM_SMCTRL2_WAITEN | FLEXPWM_SMCTRL2_DBGEN; //from pwm.c
    FLEXPWM2_OUTEN = 0x0110;  // Enable PWM_A and PWM_B outputs.
    
    //CONFIGURAR LOS VALORES DE COMPARACIÓN
    FLEXPWM2_SM0INIT = 0; //Initial value: 0
    calculateRegistersFLEXPWM_TPT(i_PS);
    //这个i_PS到底是干啥的
    //From pwm.c - No idea what this is for
    FLEXPWM2_FCTRL0 = FLEXPWM_FCTRL0_FLVL(15); // logic high = fault
    FLEXPWM2_FSTS0 = 0x000F; // clear fault status
    FLEXPWM2_FFILT0 = 0;    
    FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
    //MCTRL更新了 fault status赋值
    //FLEXPWM2_SM0VAL0 = ST1_VAL[0];
    FLEXPWM2_SM0VAL0 = 0;
    FLEXPWM2_SM0VAL1 = ST1_VAL[1];
    FLEXPWM2_SM0VAL2 = ST1_VAL[2];
    FLEXPWM2_SM0VAL3 = ST1_VAL[3];
    FLEXPWM2_SM0VAL4 = ST1_VAL[4];
    FLEXPWM2_SM0VAL5 = ST1_VAL[5];
    FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15);
    //MCTRL更新了SM0VAL0-6的所有赋值
    //所以输出到底在哪？？？ PWMA？
    
    //Reset counter values
    FLEXPWM2_SM0CNT = 0;
    
    current_n = 0;
    FLEXPWM2_SM0STS = 0x1000; //Clear interrupt flag by writing a logic 1 to it
    FLEXPWM2_SM0STS = 0xFFFF; //Clear interrupt flags
    FLEXPWM2_SM0INTEN = 0x1000; // RELOAD INTERRUPT ENABLE  
    
    attachInterruptVector(IRQ_FLEXPWM2_0, &isr_FLEXPWM2_PWM_RELOAD0_TPT); //Attach the interrupt.
    NVIC_ENABLE_IRQ(IRQ_FLEXPWM2_0);
    
    //Configure Pins - needed here?
    configurePinsFLEXPWM();
    //Set Fault / STOP mode of PWM outputs
    FLEXPWM2_SM0OCTRL = 0;
  }else{
    Serial.println("Error - no prescaler");
  }
}

FASTRUN void isr_FLEXPWM2_PWM_RELOAD0_IND(){
  if(FLEXPWM2_SM0STS & FLEXPWM_SMSTS_RF){ //If RELOAD FLAG IS SET
    FLEXPWM2_SM0STS |= FLEXPWM_SMSTS_RF; //Clear flag by writing a logic 1 to it (0x1000)
    //do things
    current_n++;
    if(current_n > 1){
      stopFLEXPWM();
    }
    else if(current_n == 1){
      //Serial.println(FLEXPWM2_SM0VAL2,DEC); //To check what was the last value
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
      FLEXPWM2_SM0VAL0 = 0;
      FLEXPWM2_SM0VAL1 = 0;
      FLEXPWM2_SM0VAL2 = 0;
      FLEXPWM2_SM0VAL3 = 0;
      FLEXPWM2_SM0VAL4 = 0;
      FLEXPWM2_SM0VAL5 = 0;
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values (Stage III+IV)
    }
  }else{  //COMPARE VAL3 or VAL5 TRIGGERED THE INTERRUPT
    //Clear flag(s)
    FLEXPWM2_SM0STS |= (FLEXPWM_SMSTS_CMPF(3) | FLEXPWM_SMSTS_CMPF(5));
    if(!S){ //Positive
      FLEXPWM2_OUTEN = 0x0010;  //Disable PWMA
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
      FLEXPWM2_SM0VAL5 = 0;
      FLEXPWM2_SM0VAL3 = 0;
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values (Stage III+IV)
    }else{
      //Make sure that 
      FLEXPWM2_OUTEN = 0x0100; //Disable PWMB
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
      FLEXPWM2_SM0VAL5 = 0;
      FLEXPWM2_SM0VAL3 = 0;
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values (Stage III+IV)
    }
  }
}

void configureFLEXPWM_IND(){
  Serial.println("Debug - Entering configureFLEXPWM_IND()");
  //Register names defined in imxrt.h
  //Find out the duration of the test
  unsigned long test_duration = 2 * TL; //ns
  //Pick the smallest prescaler that allows holding the longest stage
  char prescalers[] = {1, 2, 4, 8, 16, 32, 64, 128};
  char prescaler;
  bool error_no_prescaler = true; //In case a stage is so long that can't fit
  unsigned long max_counter_times[] = {436900, 873800, 1747600, 3495200, 6990400,
                                       13980800, 27961600, 55923200};
  char i_PS;
  for(i_PS = 0; i_PS <= 7; i_PS++){
    if(test_duration <= max_counter_times[i_PS]){
      prescaler = prescalers[i_PS];
      error_no_prescaler = false;
      break;
    }
  }
  if(!error_no_prescaler){
    FLEXPWM2_SM0CTRL = ((uint16_t)(1<<10)) | (((uint16_t)i_PS)<<4); // Prescaler
    FLEXPWM2_SM0CTRL2 = FLEXPWM_SMCTRL2_INDEP | FLEXPWM_SMCTRL2_WAITEN | FLEXPWM_SMCTRL2_DBGEN; //from pwm.c
    FLEXPWM2_OUTEN = 0x0110;  // Enable PWM_A and PWM_B outputs.
    
    //CONFIGURE COMPARATION VALUES
    FLEXPWM2_SM0INIT = 0; //Initial value: 0
    Serial.print("Debug - i_PS: ");
    Serial.println(i_PS, DEC);
    calculateRegistersFLEXPWM_IND(i_PS);
    
    FLEXPWM2_FCTRL0 = FLEXPWM_FCTRL0_FLVL(15); // logic high = fault (from pwm.c - no idea what this is for)
    FLEXPWM2_FSTS0 = 0x000F; // clear fault status
    FLEXPWM2_FFILT0 = 0;
    
    FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
    //FLEXPWM2_SM0VAL0 = ST1_VAL[0];
    FLEXPWM2_SM0VAL0 = 0;
    FLEXPWM2_SM0VAL1 = IND_VAL[1];
    FLEXPWM2_SM0VAL2 = IND_VAL[2];
    FLEXPWM2_SM0VAL3 = IND_VAL[3];
    FLEXPWM2_SM0VAL4 = IND_VAL[4];
    FLEXPWM2_SM0VAL5 = IND_VAL[5];
    FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15);

    //Reset counter values
    FLEXPWM2_SM0CNT = 0;
    
    current_n = 0;
    FLEXPWM2_SM0STS = 0x1000; //Clear interrupt flag by writing a logic 1 to it
    FLEXPWM2_SM0STS = 0xFFFF; //Clear interrupt flags
    FLEXPWM2_SM0INTEN = 0x1000; // RELOAD INTERRUPT ENABLE
    //ENABLE INTERRUPT COMPARE
    if(!S){ //COMPARE INTERRUPT ENABLE
        FLEXPWM2_SM0INTEN |= FLEXPWM_SMSTS_CMPF(3); // ALSO ENABLE INTERRUPT ON COMPARE VAL 3
    }else{
        FLEXPWM2_SM0INTEN |= FLEXPWM_SMSTS_CMPF(5); // ALSO ENABLE INTERRUPT ON COMPARE VAL 5
    }
    //Interrupt on reload
    attachInterruptVector(IRQ_FLEXPWM2_0, &isr_FLEXPWM2_PWM_RELOAD0_IND); //Attach the interrupt.
    NVIC_ENABLE_IRQ(IRQ_FLEXPWM2_0);
    //Configure Pins - needed here?
    configurePinsFLEXPWM();
    //Set Fault / STOP mode of PWM outputs
    FLEXPWM2_SM0OCTRL = 0;
  }else{
    Serial.println("Error - no prescaler");
  }
}


void calculateRegistersFLEXPWM_IND(char i_PS){
  //For a given prescaler and Inductance Test parameters, fill in IND_VAL
  unsigned long F_BUS = F_BUS_ACTUAL; //in Hertz
  //Calculate the PWM clock period resolution for each prescaler
  //That is, how many nanoseconds lasts a prescaled PWM clock period
  double counter_clock_resolution_ns[8];
  for(char i = 0; i < 8; i++){
    counter_clock_resolution_ns[i] = ((double)1000000000 * (double)(1<<i)) / ((double)F_BUS);
  }
  // t_ns: counter clock
  // WARNING [21/06/2022] for prescalers 1, 2, 4 it works. For higher prescalers it doesn't
  // due to calculation problems (the counter period becomes large and it causes resolution
  // problems with the deadtime duration.
  // TO-DO: IMPLEMENT DEAD-TIME USING THE DEAD-TIME INSERTION SUBMODULE OF THE PWMFLEX MODULE
  // For the moment I have changed the maximum TL time [ns]: from 2000000000 to 1747600
  double t_ns = counter_clock_resolution_ns[i_PS]; // HAY QIE HACERLO EN i, no en prescaler
  Serial.print("t_ns: "); Serial.print(t_ns, DEC); Serial.println(" ns");
  if(S == 0){ //Positive (begin with PWMA)
    IND_VAL[1] = (unsigned short)((double)(2 * TL) / t_ns);
    IND_VAL[2] = (unsigned short)((double)(DT / 2) / t_ns);
    IND_VAL[3] = (unsigned short)((double)(TL - DT / 2) / t_ns);
    IND_VAL[4] = (unsigned short)((double)(TL + DT / 2) / t_ns);
    IND_VAL[5] = (unsigned short)((double)(2 * TL - DT / 2) / t_ns);
    //IND_VAL[0] = IND_VAL[4];
    IND_VAL[0] = (unsigned short)((double)(TL) / t_ns);
    // Until the HW dead-time insertion code is implemented, this piece of code
    // ensures that the deadtime is really >= DT (avoiding rounding errors)
    double deadtime_ns = (double)((IND_VAL[4]-IND_VAL[3]) * t_ns);
    char aux = 0;
    while(deadtime_ns < DT){
      if(aux % 2 == 0){
        IND_VAL[4]++;
      }else{
        IND_VAL[3]--;
      }
      aux++;
      deadtime_ns = (double)((IND_VAL[4]-IND_VAL[3]) * t_ns);
    }
  }else{  //Negative (begin with PWMB)
    IND_VAL[1] = (unsigned short)((double)(2 * TL) / t_ns);
    IND_VAL[2] = (unsigned short)((double)(TL + DT / 2) / t_ns);
    IND_VAL[3] = (unsigned short)((double)(2 * TL - DT / 2) / t_ns);
    IND_VAL[4] = (unsigned short)((double)(DT / 2) / t_ns);
    IND_VAL[5] = (unsigned short)((double)(TL - DT / 2) / t_ns);
    //IND_VAL[0] = IND_VAL[2];
    IND_VAL[0] = (unsigned short)((double)(TL) / t_ns);
    // Until the HW dead-time insertion code is implemented, this piece of code
    // ensures that the deadtime is really >= DT (avoiding rounding errors)
    double deadtime_ns = (double)((IND_VAL[2]-IND_VAL[5]) * t_ns);
    char aux = 0;
    while(deadtime_ns < DT){
      if(aux % 2 == 0){
        IND_VAL[2]++;
      }else{
        IND_VAL[5]--;
      }
      aux++;
      deadtime_ns = (double)((IND_VAL[2]-IND_VAL[5]) * t_ns);
    }
  }
    Serial.println("-- INDUCTANCE TEST --");
    for(char i = 0; i < 6; i++){
      Serial.print("VAL");  Serial.print(i,DEC);  Serial.print(": "); Serial.println(IND_VAL[i], DEC);
    }
}


FASTRUN void isr_FLEXPWM2_PWM_RELOAD0_IND_DEADTIME_INSERTION(){
  if(FLEXPWM2_SM0STS & FLEXPWM_SMSTS_RF){ //If RELOAD FLAG IS SET
    FLEXPWM2_SM0STS |= FLEXPWM_SMSTS_RF; //Clear flag by writing a logic 1 to it (0x1000)
    //do things
    current_n++;
    if(current_n > 1){
      stopFLEXPWM();
    }
    else if(current_n == 1){
      //Serial.println(FLEXPWM2_SM0VAL2,DEC); //To check what was the last value
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
      FLEXPWM2_SM0VAL0 = 0;
      FLEXPWM2_SM0VAL1 = 0;
      FLEXPWM2_SM0VAL2 = 0;
      FLEXPWM2_SM0VAL3 = 0;
      FLEXPWM2_SM0VAL4 = 0;
      FLEXPWM2_SM0VAL5 = 0;
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values (Stage III+IV)
    }
  }else{  //COMPARE VAL3 or VAL5 TRIGGERED THE INTERRUPT -> Getting ready to switch ouputs off
    //Clear flag(s)
    FLEXPWM2_SM0STS |= (FLEXPWM_SMSTS_CMPF(3) | FLEXPWM_SMSTS_CMPF(2));
    if(!S){ //Positive
      FLEXPWM2_OUTEN = 0x0010;  //Disable PWMA
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
      FLEXPWM2_SM0VAL2 = 0;
      FLEXPWM2_SM0VAL3 = 0;
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values
    }else{
      //Make sure that 
      FLEXPWM2_OUTEN = 0x0100; //Disable PWMB
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
      FLEXPWM2_SM0VAL2 = 0;
      FLEXPWM2_SM0VAL3 = 0;
      FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values
    }
  }
}

void configureFLEXPWM_IND_DEADTIME_INSERTION(){
  // This function and calculateRegistersFLEXPWM_IND_DEADTIME_INSERTION() are under development. 
  // They treat the PWM outputs as complementary pairs, eliminating the need to include 
  // the deadtime in the calculations.
  // Once implemented and tested the documentation should be updated too (timing diagrams)
  
  Serial.println("Debug - Entering configureFLEXPWM_IND_DEADTIME_INSERTION()");
  //Register names defined in imxrt.h
  //Find out the duration of the test
  unsigned long test_duration = 2 * TL; //ns
  //Pick the smallest prescaler that allows holding the longest stage
  char prescalers[] = {1, 2, 4, 8, 16, 32, 64, 128};
  char prescaler;
  bool error_no_prescaler = true; //In case a stage is so long that can't fit
  unsigned long max_counter_times[] = {436900, 873800, 1747600, 3495200, 6990400,
                                       13980800, 27961600, 55923200};
  char i_PS;
  for(i_PS = 0; i_PS <= 7; i_PS++){
    if(test_duration <= max_counter_times[i_PS]){
      prescaler = prescalers[i_PS];
      error_no_prescaler = false;
      break;
    }
  }
  if(!error_no_prescaler){
    //DEADTIME
    // If the fractional delays are off, then the upper 5 bits of DTCNT0 are ignored and the
    //remaining 11 bits are used to specify the number of cycles of deadtime.
    unsigned long F_BUS = F_BUS_ACTUAL; //in Hertz
    double T_BUS_ns = ((double)1000000000) / ((double)F_BUS);
    unsigned short deadtime_cycles = ceil((unsigned short)((double)DT / T_BUS_ns));
    // Verify - should be 12 F_BUS cycles to achieve 80 ns deadtime.
    // DTCNT0 field - deadtime during 0 to 1 transitions of the PWM_A output (normal polarity)
    // DTCNT1 field - deadtime during 0 to 1 transitions of the PWM_B output (normal polarity)
    FLEXPWM2_SM0DTCNT0 = deadtime_cycles; 
    FLEXPWM2_SM0DTCNT1 = deadtime_cycles; 
    
    FLEXPWM2_SM0CTRL = ((uint16_t)(1<<10)) | (((uint16_t)i_PS)<<4); // Prescaler
    
    //Not INDEP -> COMPLEMENTARY MODE
    FLEXPWM2_SM0CTRL2 = ~FLEXPWM_SMCTRL2_INDEP | FLEXPWM_SMCTRL2_WAITEN | FLEXPWM_SMCTRL2_DBGEN; //from pwm.c
    
    FLEXPWM2_OUTEN = 0x0110;  // Enable PWM_A and PWM_B outputs.
    
    //CONFIGURE COMPARATION VALUES
    FLEXPWM2_SM0INIT = 0; //Initial value: 0
    Serial.print("Debug - i_PS: ");
    Serial.println(i_PS, DEC);
    calculateRegistersFLEXPWM_IND(i_PS);
    
    FLEXPWM2_FCTRL0 = FLEXPWM_FCTRL0_FLVL(15); // logic high = fault (from pwm.c - no idea what this is for)
    FLEXPWM2_FSTS0 = 0x000F; // clear fault status
    FLEXPWM2_FFILT0 = 0;
    
    FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
    //FLEXPWM2_SM0VAL0 = ST1_VAL[0];
    FLEXPWM2_SM0VAL0 = 0;
    FLEXPWM2_SM0VAL1 = IND_VAL[1];
    FLEXPWM2_SM0VAL2 = IND_VAL[2];
    FLEXPWM2_SM0VAL3 = IND_VAL[3];
    FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15);

    //Reset counter values
    FLEXPWM2_SM0CNT = 0;
    
    current_n = 0;
    FLEXPWM2_SM0STS = 0x1000; //Clear interrupt flag by writing a logic 1 to it
    FLEXPWM2_SM0STS = 0xFFFF; //Clear interrupt flags
    FLEXPWM2_SM0INTEN = 0x1000; // RELOAD INTERRUPT ENABLE
    //ENABLE INTERRUPT COMPARE
    // TO-DO FOR DEAD-TIME INSERTION / For complementary operation the values should be others
    // I believe it's COMP VAL 3 for positive, COMP VAL 2 for negative
    // In any case, we'll also need to update the ISR routine (isr_FLEXPWM2_PWM_RELOAD0_IND_DEADTIME_INSERTION)
    // Delete these comments when fully tested
    if(!S){ //COMPARE INTERRUPT ENABLE
        FLEXPWM2_SM0INTEN |= FLEXPWM_SMSTS_CMPF(3); // ENABLE INTERRUPT ON COMPARE VAL 3
    }else{
        FLEXPWM2_SM0INTEN |= FLEXPWM_SMSTS_CMPF(2); // ENABLE INTERRUPT ON COMPARE VAL 2
    }
    //Interrupt on reload
    // TO-DO: update ad-hoc ISR
    attachInterruptVector(IRQ_FLEXPWM2_0, &isr_FLEXPWM2_PWM_RELOAD0_IND_DEADTIME_INSERTION); //Attach the interrupt.
    NVIC_ENABLE_IRQ(IRQ_FLEXPWM2_0);
    //Configure Pins - needed here?
    configurePinsFLEXPWM();
    //Set Fault / STOP mode of PWM outputs
    FLEXPWM2_SM0OCTRL = 0;
  }else{
    Serial.println("Error - no prescaler");
  }
}


void calculateRegistersFLEXPWM_IND_DEADTIME_INSERTION(char i_PS){
  // This function and configureFLEXPWM_IND_DEADTIME_INSERTION() are under development. They treat 
  // the PWM outputs as complementary pairs, eliminating the need to include the deadtime
  // in the calculations.
  // Once implemented and tested the documentation should be updated too (timing diagrams)
  
  //For a given prescaler and Inductance Test parameters, fill in IND_VAL
  unsigned long F_BUS = F_BUS_ACTUAL; //in Hertz
  //Calculate the PWM clock period resolution for each prescaler
  //That is, how many nanoseconds lasts a prescaled PWM clock period
  double counter_clock_resolution_ns[8];
  for(char i = 0; i < 8; i++){
    counter_clock_resolution_ns[i] = ((double)1000000000 * (double)(1<<i)) / ((double)F_BUS);
  }
  // t_ns: counter clock
  // WARNING [21/06/2022] for prescalers 1, 2, 4 it works. For higher prescalers it doesn't
  // due to calculation problems (the counter period becomes large and it causes resolution
  // problems with the deadtime duration.
  // TO-DO: IMPLEMENT DEAD-TIME USING THE DEAD-TIME INSERTION SUBMODULE OF THE PWMFLEX MODULE
  // For the moment I have changed the maximum TL time [ns]: from 2000000000 to 1747600
  double t_ns = counter_clock_resolution_ns[i_PS]; // HAY QIE HACERLO EN i, no en prescaler
  Serial.print("t_ns: "); Serial.print(t_ns, DEC); Serial.println(" ns");
  if(S == 0){ //Positive (begin with PWMA)
    IND_VAL[1] = (unsigned short)((double)(2 * TL) / t_ns);
    //IND_VAL[2] = (unsigned short)((double)(DT / 2) / t_ns);
    IND_VAL[2] = 0;
    IND_VAL[3] = (unsigned short)((double)(TL) / t_ns);
    //IND_VAL[0] = IND_VAL[4];
    IND_VAL[0] = (unsigned short)((double)(TL) / t_ns);
  }else{  //Negative (begin with PWMB)
    IND_VAL[1] = (unsigned short)((double)(2 * TL) / t_ns);
    IND_VAL[2] = (unsigned short)((double)(TL) / t_ns);
    //IND_VAL[3] = (unsigned short)((double)(2 * TL - DT / 2) / t_ns);
    IND_VAL[3] = 0;
    //IND_VAL[0] = IND_VAL[2];
    IND_VAL[0] = (unsigned short)((double)(TL) / t_ns);
  }
    Serial.println("-- INDUCTANCE TEST (DEADTIME INSERTION) --");
    for(char i = 0; i < 4; i++){
      Serial.print("VAL");  Serial.print(i,DEC);  Serial.print(": "); Serial.println(IND_VAL[i], DEC);
    }
}

void configureFLEXPWM_DEMAG(){
  Serial.println("Debug - Entering configureFLEXPWM_DEMAG()");
  //Register names defined in imxrt.h
  //Find out the duration of the test
  unsigned long test_duration = 2 * TL; //ns
  //Pick the smallest prescaler that allows holding the longest stage
  char prescalers[] = {1, 2, 4, 8, 16, 32, 64, 128};
  char prescaler;
  bool error_no_prescaler = true; //In case a stage is so long that can't fit
  unsigned long max_counter_times[] = {436900, 873800, 1747600, 3495200, 6990400,
                                       13980800, 27961600, 55923200};
  char i_PS;
  for(i_PS = 0; i_PS <= 7; i_PS++){
    if(test_duration <= max_counter_times[i_PS]){
      prescaler = prescalers[i_PS];
      error_no_prescaler = false;
      break;
    }
  }
  if(!error_no_prescaler){
    FLEXPWM2_SM0CTRL = ((uint16_t)(1<<10)) | (((uint16_t)i_PS)<<4); // Prescaler
    FLEXPWM2_SM0CTRL2 = FLEXPWM_SMCTRL2_INDEP | FLEXPWM_SMCTRL2_WAITEN | FLEXPWM_SMCTRL2_DBGEN; //from pwm.c
    FLEXPWM2_OUTEN = 0x0110;  // Enable PWM_A and PWM_B outputs.
    
    //CONFIGURAR LOS VALORES DE COMPARACIÓN
    FLEXPWM2_SM0INIT = 0; //Initial value: 0
    Serial.print("Debug - i_PS: ");
    Serial.println(i_PS, DEC);
    calculateRegistersFLEXPWM_IND(i_PS);  // We use the same fuction for demagnetization and inductance - TODO: consistent naming
    
    FLEXPWM2_FCTRL0 = FLEXPWM_FCTRL0_FLVL(15); // logic high = fault (from pwm.c - no idea what this is for)
    FLEXPWM2_FSTS0 = 0x000F; // clear fault status
    FLEXPWM2_FFILT0 = 0;
    
    FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
    //FLEXPWM2_SM0VAL0 = ST1_VAL[0];
    FLEXPWM2_SM0VAL0 = 0;
    FLEXPWM2_SM0VAL1 = IND_VAL[1];
    FLEXPWM2_SM0VAL2 = IND_VAL[2];
    FLEXPWM2_SM0VAL3 = IND_VAL[3];
    FLEXPWM2_SM0VAL4 = IND_VAL[4];
    FLEXPWM2_SM0VAL5 = IND_VAL[5];
    FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15);

    //Reset counter values
    FLEXPWM2_SM0CNT = 0;
    
    current_n = 0;
    FLEXPWM2_SM0STS = 0x1000; //Clear interrupt flag by writing a logic 1 to it
    FLEXPWM2_SM0STS = 0xFFFF; //Clear interrupt flags
    FLEXPWM2_SM0INTEN = 0x1000; // RELOAD INTERRUPT ENABLE
    /* 2022/07/188 - COMMENTED INTERRUPT - AFTER STOPWMFLEX I'LL NEED TO DISABLE THE DIGITAL OUTPUTS ON THE MAIN CODE
    //ENABLE INTERRUPT COMPARE
    if(!S){ //COMPARE INTERRUPT ENABLE
        FLEXPWM2_SM0INTEN |= FLEXPWM_SMSTS_CMPF(3); // ALSO ENABLE INTERRUPT ON COMPARE VAL 3
    }else{
        FLEXPWM2_SM0INTEN |= FLEXPWM_SMSTS_CMPF(5); // ALSO ENABLE INTERRUPT ON COMPARE VAL 5
    }
    //Interrupt on reload
    attachInterruptVector(IRQ_FLEXPWM2_0, &isr_FLEXPWM2_PWM_RELOAD0_IND); //Attach the interrupt.
    NVIC_ENABLE_IRQ(IRQ_FLEXPWM2_0);
    */
    //Configure Pins - needed here?
    configurePinsFLEXPWM();
    //Set Fault / STOP mode of PWM outputs
    FLEXPWM2_SM0OCTRL = 0;
  }else{
    Serial.println("Error - no prescaler");
  }
}

// ======= STATES AND TRANSITIONS DEFINITION ========= //

void state0() {
  Serial.println("S0 - INIT");
  enableLED(0);
  // Configure half-bridge pins - definition in PWM_Pins_Register_Configuration.xlsx
  configurePinsFLEXPWM();
  delay(500);
}

bool transitionS0S1() {
  eval = true;
  if (eval)
    new_state = 1;
  return eval;
}

//-------------------------

void state1() {
  if (new_state) {  // Code that will be executed only once
    Serial.println("S1 - Serial Listener");
    enableLED(1);
    new_state = 0;
  }
  while (Serial.available() > 0) {
    delay(100);              //Delay to allow time to receive the whole message
    p = Serial.read();
    switch (p) {
      case 'A':
        received_command = 'A';
        received_parameter = Serial.parseInt();
        break;
      case 'B':
        received_command = 'B';
        received_parameter = Serial.parseInt();
        break;
      case 'C':
        received_command = 'C';
        received_parameter = Serial.parseInt();
        break;
      case 'D':
        received_command = 'D';
        received_parameter = Serial.parseInt();
        break;
      case 'E':
        received_command = 'E';
        received_parameter = Serial.parseInt();
        break;
      case 'F':
        received_command = 'F';
        received_parameter = Serial.parseInt();
        break;
      case 'G':
        received_command = 'G';
        received_parameter = Serial.parseInt();
        break;
      case 'H':
        received_command = 'H';
        received_parameter = Serial.parseInt();
        break;
      case 'T':
        received_command = 'T';
        break;
      case 'L':
        received_command = 'L';
        break;
      case 'R':
        received_command = 'R';
        received_parameter = Serial.parseInt();
        break;
      case 'M':
        received_command = 'M';
        break;
      case 'K':
        received_command = 'K';
        break;
      case 'W':
        received_command = 'W';
        break;
      case 'Z':
        received_command = 'Z';
        break;
      default:
        received_command = '0'; //No command
    }
    while (Serial.read() >= 0)  // Clear buffer
          ; // do nothing
  }
}

bool transitionS1S2() {
  eval = (received_command == 'A') || (received_command == 'B') || (received_command == 'C') || (received_command == 'D') ||
         (received_command == 'E') || (received_command == 'F') || (received_command == 'G') || (received_command == 'H');
  if (eval)
    new_state = 1;
  return eval;
}

bool transitionS1S3() {
  eval = (received_command == 'T');
  if (eval)
    new_state = 1;
  return eval;
}

bool transitionS1S4() {
  eval = (received_command == 'L');
  if (eval)
    new_state = 1;
  return eval;
}

bool transitionS1S5() {
  eval = (received_command == 'R');
  if (eval)
    new_state = 1;
  return eval;
}

bool transitionS1S6() {
  eval = (received_command == 'M');
  if (eval)
    new_state = 1;
  return eval;
}

bool transitionS1S7() {
  eval = (received_command == 'K');
  if (eval)
    new_state = 1;
  return eval;
}

bool transitionS1S8() {
  eval = (received_command == 'Z');
  if (eval)
    new_state = 1;
  return eval;
}

bool transitionS1S9() {
  eval = (received_command == 'W');
  if (eval)
    new_state = 1;
  return eval;
}

//-------------------------

void state2() {
  if (new_state) {  // Code that will be executed only once
    Serial.println("S2 - Store Parameter");
    Serial.print("Received command: ");
    Serial.println(received_command);
    Serial.print("Received parameter: ");
    Serial.println(received_parameter);
    enableLED(2);
    new_state = 0;
    //Process parameters - check that the value is positive and within range,
    // and when needed round them to the closest mulitple of 5 ns
    switch (received_command) {
      case 'A': //T1
        if((received_parameter >= 0) && (received_parameter <= 2000000000)){
          T1 = (unsigned long)received_parameter;
          // T1 = 5 * ((T1 + 2) / 5);  // Ratchet decimation + rounding
          T1_OK = true;
        }else{
          T1 = 0;
          T1_OK = false;
        }
        break;
      case 'B': //T2A
        if((received_parameter >= 50) && (received_parameter <= 2000000000)){
          T2A = (unsigned long)received_parameter;
          T2A_OK = true;
        }else{
          T2A = 0;
          T2A_OK = false;
        }
        break;
      case 'C': //T2B
        if((received_parameter >= 50) && (received_parameter <= 2000000000)){
          T2B = (unsigned long)received_parameter;
          T2B_OK = true;
        }else{
          T2B = 0;
          T2B_OK = false;
        }
        break;
      case 'D': //T3
        if((received_parameter >= 0) && (received_parameter <= 2000000000)){
          T3 = (unsigned long)received_parameter;
          T3_OK = true;
        }else{
          T3 = 0;
          T3_OK = false;
        }
        break;
      case 'E': //TL
        //if((received_parameter >= 50) && (received_parameter <= 2000000000)){
        if((received_parameter >= 50) && (received_parameter <= 1747600)){
          TL = (unsigned long)received_parameter;
          TL_OK = true;
        }else{
          TL = 100;
          TL_OK = false;
        }
        break;
      case 'F': //N
        if((received_parameter >= 1) && (received_parameter <= 255)){
          N = (byte)received_parameter;
          N_OK = true;
        }else{
          N = 0;
          N_OK = false;
        }
        break;
      case 'G': //S
        if((received_parameter == 0) || (received_parameter == 1)){
          S = (bool)received_parameter;
          S_OK = true;
        }else{
          S = 0;
          S_OK = false;
        }
        break;
      case 'H': //CORE_DEMAG_SCHEDULED
        if((received_parameter == 0) || (received_parameter == 1)){
          CORE_DEMAG_SCHEDULED = (bool)received_parameter;
          CORE_DEMAG_SCHEDULED_OK = true;
        }else{
          CORE_DEMAG_SCHEDULED = 0;
          CORE_DEMAG_SCHEDULED_OK = false;
        }
        break;
    }
  }
  received_command = '0'; //Resets command.
  delay(50);
}

bool transitionS2S1() {
  eval = true;
  if (eval)
    new_state = 1;
  return eval;
}

//-------------------------

void state3() {
  if (new_state) {  // Code that will be executed only once
    Serial.println("S3 - Triple Pulse Test");
    enableLED(3);
    new_state = 0;
    received_command = '0'; //Resets command.
    //printFLEXPWMRegisters();
    //delay(1000);
    // Triple Pulse Test
    // PWM Register configuration
    configureFLEXPWM_TPT();
    delayMicroseconds(10);
    initFLEXPWM();
  }
  //delay(500);
}

bool transitionS3S1() {
  eval = true;
  if (eval)
    new_state = 1;
  return eval;
}

//-------------------------

void state4() {
  if (new_state) {  // Code that will be executed only once
    Serial.println("S4 - Inductance Test");
    enableLED(4);
    new_state = 0;
    received_command = '0'; //Resets command.
    //printFLEXPWMRegisters();VAL
    //delay(1000);
    // Inductance Test
    // PWM Register configuration
    configureFLEXPWM_IND();
    //delay(1000); - DEBUG - WAS ON
    delayMicroseconds(10);
    initFLEXPWM();
  }
  //delay(500);
}

bool transitionS4S1() {
  eval = true;
  if (eval)
    new_state = 1;
  return eval;
}

//-------------------------

void state5() {
  if (new_state) {  // Code that will be executed only once
    Serial.println("S5 - Set Discharge Resistor");
    enableLED(5);
    received_command = '0'; //Resets command.
    new_state = 0;
  }
  //delay(500);
}

bool transitionS5S1() {
  eval = true;
  if (eval)
    new_state = 1;
  return eval;
}

//-------------------------

void state6() {
  if (new_state) {  // Code that will be executed only once
    Serial.println("S6 - Demagnetize Probe");
    enableLED(6);
    received_command = '0'; //Resets command.
    new_state = 0;
    printFLEXPWMRegisters();
  }
  //delay(500);
}

bool transitionS6S1() {
  eval = true;
  if (eval)
    new_state = 1;
  return eval;
}

//-------------------------

void state7() {
  if (new_state) {  // Code that will be executed only once
    Serial.println("S7 - Read Memory");
    enableLED(7);
    received_command = '0'; //Resets command.
    new_state = 0;
    Serial.println("===== MEMORY CONTENT ======");
    
    Serial.print("T1: "); Serial.print(T1); Serial.print(" ns - STATUS: ");
    if(T1_OK){
      Serial.println("OK");
    }else{
      Serial.println("ERROR");
    }
    
    Serial.print("T2A: "); Serial.print(T2A); Serial.print(" ns - STATUS: ");
    if(T2A_OK){
      Serial.println("OK");
    }else{
      Serial.println("ERROR");
    }
    
    Serial.print("T2B: "); Serial.print(T2B); Serial.print(" ns - STATUS: ");
    if(T2A_OK){
      Serial.println("OK");
    }else{
      Serial.println("ERROR");
    } 
    
    Serial.print("T3: "); Serial.print(T3); Serial.print(" ns - STATUS: ");
    if(T3_OK){
      Serial.println("OK");
    }else{
      Serial.println("ERROR");
    }
    
    Serial.print("TL: "); Serial.print(TL); Serial.print(" ns - STATUS: ");
    if(TL_OK){
      Serial.println("OK");
    }else{
      Serial.println("ERROR");
    }
    
    Serial.print("N: "); Serial.print(N); Serial.print(" cycles - STATUS: ");
    if(N_OK){
      Serial.println("OK");
    }else{
      Serial.println("ERROR");
    }
    
    Serial.print("S: "); Serial.print(S); 
    if(S){
      Serial.print(" (NEG) - STATUS: ");
    }else{
      Serial.print(" (POS) - STATUS: ");      
    }
    if(T2B_OK){
      Serial.println("OK");
    }else{
      Serial.println("ERROR");
    }

    Serial.print("CORE_DEMAG_SCHEDULED: "); Serial.print(CORE_DEMAG_SCHEDULED); 
    if(CORE_DEMAG_SCHEDULED){
      Serial.print(" (YES) - STATUS: ");
    }else{
      Serial.print(" (NO) - STATUS: ");      
    }
    if(CORE_DEMAG_SCHEDULED_OK){
      Serial.println("OK");
    }else{
      Serial.println("ERROR");
    }

    Serial.print("CORE_DEMAG_ENABLED: "); Serial.print(CORE_DEMAG_ENABLED); 
    if(CORE_DEMAG_ENABLED){
      Serial.println(" (ENABLED) - STATUS: N/A");
    }else{
      Serial.println(" (NOT ENABLED) - STATUS: N/A");      
    }
  }
  //delay(500);
}

bool transitionS7S1() {
  eval = true;
  if (eval)
    new_state = 1;
  return eval;
}

//-------------------------

void state8() {
  if (new_state) {  // Code that will be executed only once
    Serial.println("S8 - Reset");
    enableLED(0);
    received_command = '0'; //Resets command.
    delay(100);
    new_state = 0;
    SCB_AIRCR = 0x05FA0004;
  }
}

//--------------------------

void state9() {
  if (new_state) {  // Code that will be executed only once
    Serial.println("S9 - Core Demagnetization");
    enableLED(2);
    received_command = '0'; //Resets command.
    delay(100);
    new_state = 0;
    if (CORE_DEMAG_SCHEDULED && !CORE_DEMAG_ENABLED){  // Enable PWM Timer
      CORE_DEMAG_SCHEDULED = false;
      CORE_DEMAG_ENABLED = true;
      configureFLEXPWM_DEMAG(); //Adapt routines for continuous operation - future function: configureFLEXPWM_DEMAG()
      delayMicroseconds(10);
      initFLEXPWM();  //Adapt routines for continuous operation
    }else if(CORE_DEMAG_ENABLED){ //Disable PWM Timer and outputs
      // Disable PWM Timer
      CORE_DEMAG_ENABLED = false;
      stopFLEXPWM();
      // Following if/else: added on 2022/18/07 to disable channels - verify.
      if(!S){ //Positive
        FLEXPWM2_OUTEN = 0x0010;  //Disable PWMA
        FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
        FLEXPWM2_SM0VAL5 = 0;
        FLEXPWM2_SM0VAL3 = 0;
        FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values (Stage III+IV)
      }else{
        //Make sure that 
        FLEXPWM2_OUTEN = 0x0100; //Disable PWMB
        FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_CLDOK(15);
        FLEXPWM2_SM0VAL5 = 0;
        FLEXPWM2_SM0VAL3 = 0;
        FLEXPWM2_MCTRL |= FLEXPWM_MCTRL_LDOK(15); //LOAD current values (Stage III+IV)
      }
    }
  }
}

bool transitionS9S1() {
  eval = true;
  if (eval)
    new_state = 1;
  return eval;
}

//--------------------------

void printFLEXPWMRegisters(){
  Serial.println("---- FLEXPWM REGISTERS ----");
  Serial.print("FLEXPWM2_SM0CNT: "); Serial.println(FLEXPWM2_SM0CNT, DEC);
  Serial.print("FLEXPWM2_SM0INIT: "); Serial.println(FLEXPWM2_SM0INIT, DEC);
  Serial.print("FLEXPWM2_SM0CTRL2: "); Serial.println(FLEXPWM2_SM0CTRL2, HEX);
  Serial.print("FLEXPWM2_SM0CTRL: "); Serial.println(FLEXPWM2_SM0CTRL, HEX);
  Serial.print("FLEXPWM2_SM0VAL0: "); Serial.println(FLEXPWM2_SM0VAL0, DEC);
  Serial.print("FLEXPWM2_SM0VAL1: "); Serial.println(FLEXPWM2_SM0VAL1, DEC);
  Serial.print("FLEXPWM2_SM0VAL2: "); Serial.println(FLEXPWM2_SM0VAL2, DEC);
  Serial.print("FLEXPWM2_SM0VAL3: "); Serial.println(FLEXPWM2_SM0VAL3, DEC);
  Serial.print("FLEXPWM2_SM0VAL4: "); Serial.println(FLEXPWM2_SM0VAL4, DEC);
  Serial.print("FLEXPWM2_SM0VAL5: "); Serial.println(FLEXPWM2_SM0VAL5, DEC);
  Serial.print("FLEXPWM2_SM0OCTRL: "); Serial.println(FLEXPWM2_SM0OCTRL, HEX);
  Serial.print("FLEXPWM2_SM0STS: "); Serial.println(FLEXPWM2_SM0STS, HEX);
  Serial.print("FLEXPWM2_SM0INTEN: "); Serial.println(FLEXPWM2_SM0INTEN, HEX);
  Serial.print("FLEXPWM2_SM0TCTRL: "); Serial.println(FLEXPWM2_SM0TCTRL, HEX);
  Serial.print("FLEXPWM2_SM0DISMAP0: "); Serial.println(FLEXPWM2_SM0DISMAP0, HEX);
  Serial.print("FLEXPWM2_SM0DISMAP1: "); Serial.println(FLEXPWM2_SM0DISMAP1, HEX);
  Serial.print("FLEXPWM2_OUTEN: "); Serial.println(FLEXPWM2_OUTEN, HEX);
  Serial.print("FLEXPWM2_MASK: "); Serial.println(FLEXPWM2_MASK, HEX);
  Serial.print("FLEXPWM2_SWCOUT: "); Serial.println(FLEXPWM2_SWCOUT, HEX);
  Serial.print("FLEXPWM2_DTSRCSEL: "); Serial.println(FLEXPWM2_DTSRCSEL, HEX);
  Serial.print("FLEXPWM2_MCTRL: "); Serial.println(FLEXPWM2_MCTRL, HEX);
  Serial.print("FLEXPWM2_MCTRL2: "); Serial.println(FLEXPWM2_MCTRL2, HEX);
  Serial.print("FLEXPWM2_FCTRL0: "); Serial.println(FLEXPWM2_FCTRL0, HEX);
  Serial.print("FLEXPWM2_FSTS0: "); Serial.println(FLEXPWM2_FSTS0, HEX);
  Serial.print("FLEXPWM2_FFILT0: "); Serial.println(FLEXPWM2_FFILT0, HEX);
  Serial.print("FLEXPWM2_FTST0: "); Serial.println(FLEXPWM2_FTST0, HEX);
  Serial.print("FLEXPWM2_FCTRL20: "); Serial.println(FLEXPWM2_FCTRL20, HEX);
  Serial.println("--------------------------");
}
