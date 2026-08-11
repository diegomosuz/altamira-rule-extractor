       IDENTIFICATION DIVISION.
       PROGRAM-ID. GTST001.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-SALDO                 PIC 9(7)V99.
       01 WS-MONTO                 PIC 9(7)V99.
       01 WS-ESTADO-OPERACION      PIC X(1).
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-SALDO < WS-MONTO
               MOVE 'R' TO WS-ESTADO-OPERACION
           END-IF.
