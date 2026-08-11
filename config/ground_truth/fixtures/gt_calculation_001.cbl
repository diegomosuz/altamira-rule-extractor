       IDENTIFICATION DIVISION.
       PROGRAM-ID. GTCALC001.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-TIPO             PIC X(1).
       01 WS-MONTO            PIC 9(7)V99.
       01 WS-TASA             PIC 9(3)V99.
       01 WS-COMISION         PIC 9(7)V99.
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-TIPO = 'I'
               COMPUTE WS-COMISION = WS-MONTO * WS-TASA
           END-IF.
