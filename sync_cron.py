from sync import CascadeBlockProcessor

cbp = CascadeBlockProcessor()

time_interval = 1
send_email = True
yield_output = False

cbp.process_all_blocks(time_interval, send_email, yield_output)