       IDENTIFICATION DIVISION.
       PROGRAM-ID. GTRC001.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-COD-RETORNO           PIC 9(2).
       01 WS-MONTO                 PIC 9(7)V99.
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-MONTO > 1000
               MOVE 99 TO WS-COD-RETORNO
           ELSE
               MOVE 0 TO WS-COD-RETORNO
           END-IF.
