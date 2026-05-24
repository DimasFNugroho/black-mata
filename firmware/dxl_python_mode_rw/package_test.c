/*
 * Data Structure
 * 
 * Data Name | Length
 *
 */
#include <stdio.h>
static const char  START_BYTE    = 0xAA;
static const char  CMD_HEARTBEAT = 0x01;
static const char  CMD_HB_ACK    = 0x02;
static const int   PACKET_SIZE   = 9;

enum servo_id
{
	ID_1=0x01,
	ID_2=0x02,
	ID_3=0x03,
	ID_4=0x04,
	ID_5=0x05,
	ID_6=0x06,
	ID_7=0x07,
	ID_8=0x08
};

struct servo {
	unsigned char id;
};

int main()
{
	printf("START_BYTE: %x\n", START_BYTE);
	printf("CMD_HEARTBEAT: %x\n", CMD_HEARTBEAT);
	printf("CMD_HB_ACK: %x\n", CMD_HB_ACK);
	printf("PACKET_SIZE: %d\n", PACKET_SIZE);

	unsigned char id;
	id = 256;

	printf("servo_id: %d\n", id);
}
