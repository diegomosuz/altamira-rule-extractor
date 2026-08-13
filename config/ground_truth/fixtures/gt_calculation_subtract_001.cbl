       IDENTIFICATION DIVISION.
       PROGRAM-ID. GTCALC005.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-TIPO             PIC X(1).
       01 WS-SALDO            PIC 9(7)V99.
       01 WS-RETIRO           PIC 9(7)V99.
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-TIPO = 'D'
               SUBTRACT WS-RETIRO FROM WS-SALDO
           END-IF.
