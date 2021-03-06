from .header import *

class BERTMULTIVIEW(nn.Module):
    
    '''
    Multi-view for automatic evaluation, retrieval-based dialog system and generation rerank:
    1. Fluency
    2. Coherence
    3. Diversity
    4. Naturalness
    5. Relatedness
    '''

    def __init__(self):
        super(BERTMULTIVIEW, self).__init__()
        self.model = BertModel.from_pretrained('bert-base-chinese')
        
        self.fluency_m = nn.Linear(768, 2)
        self.coherence_m = nn.Linear(768, 2)
        self.diversity_m = nn.Linear(768, 2)
        self.naturalness_m = nn.Linear(768, 2)
        self.relatedness_m = nn.Linear(768, 2)
        
        self.head = nn.Linear(2*5, 2)

    def forward(self, inpt, aspect='coherence'):
        if aspect != 'overall':
            with torch.no_grad():
                attn_mask = generate_attention_mask(inpt)
                output = self.model(input_ids=inpt, attention_mask=attn_mask)[0]
                output = torch.mean(output, dim=1)    # [batch, 768]
        else:
            attn_mask = generate_attention_mask(inpt)
            output = self.model(input_ids=inpt, attention_mask=attn_mask)[0]
            output = torch.mean(output, dim=1)    # [batch, 768]

        if aspect == 'coherence':
            coherence_rest = self.coherence_m(output)
            return coherence_rest
        elif aspect == 'fluency':
            fluency_rest = self.fluency_m(output)
            return fluency_rest
        elif aspect == 'diversity':
            diversity_rest = self.diversity_m(output)
            return diversity_rest
        elif aspect == 'naturalness':
            naturalness_rest = self.naturalness_m(output)
            return naturalness_rest
        elif aspect == 'relatedness':
            relatedness_rest = self.relatedness_m(output)
            return relatedness_rest
        elif aspect == 'overall':
            fluency_m = self.fluency_m(output)
            coherence_m = self.coherence_m(output)
            diversity_m = self.diversity_m(output)
            naturalness_m = self.naturalness_m(output)
            relatedness_m = self.relatedness_m(output)
            output = torch.cat(
                [fluency_m, coherence_m, diversity_m, naturalness_m, relatedness_m],
                dim=1,
            )    # [batch, 10]
            output = self.head(torch.relu(output))    # [batch, 2]
            return output
        else:
            raise Exception(f'[!] target aspect {aspect} is unknown')


class BERTMULTIVIEWAgent(RetrievalBaseAgent):
    
    '''
    Only train with 1 epoch: 1 epoch contains 5 warmup training and 1 fine tuning steps for each aspect.
    '''
    
    def __init__(self, multi_gpu, run_mode='train', lang='zh', kb=False):
        super(BERTMULTIVIEWAgent, self).__init__(kb=kb)
        try:
            self.gpu_ids = list(range(len(multi_gpu.split(','))))
        except:
            raise Exception(f'[!] multi gpu ids are needed, but got: {multi_gpu}')
        self.args = {
                'lr': 1e-5,
                'pad': 0,
                'vocab_file': 'data/vocab/small',
                'talk_samples': 256,
                'multi_gpu': self.gpu_ids,
                'grad_clip': 3.0,
                'samples': 10
        }
        self.vocab = BertTokenizer.from_pretrained('bert-base-chinese')
        self.model = BERTMULTIVIEW()
        if torch.cuda.is_available():
            self.model.cuda()
        self.model = DataParallel(self.model, device_ids=self.gpu_ids)
        self.optimizer = transformers.AdamW(
                self.model.parameters(),
                lr=self.args['lr'])
        self.criterion = nn.CrossEntropyLoss()

        self.show_parameters(self.args)
        
    def train_model(self, train_iters, mode='train', recoder=None):
        self.model.train()
        order = ['coherence', 'fluency', 'diversity', 'naturalness', 'relatedness']
        for aspect, iter_ in tqdm(zip(order, train_iters[:-1])):
            print(f'[!] begin train the `{aspect}` negative aspect')
            loss, acc = self.train_model_aspect(iter_, aspect=aspect)
        loss, acc = self.train_model_aspect(train_iters[-1], aspect='overall')
        return loss

    def train_model_aspect(self, train_iter, aspect='coherence'):
        batch_num = 0
        correct, s, total_loss = 0, 0, 0
        pbar = tqdm(train_iter)
        for batch in pbar:
            cid, label = batch
            self.optimizer.zero_grad()
            output = self.model(cid, aspect=aspect)    # [batch, 2]
            loss = self.criterion(
                    output, 
                    label.view(-1))
            loss.backward()
            clip_grad_norm_(self.model.parameters(), self.args['grad_clip'])
            self.optimizer.step()
                        
            total_loss += loss.item()
            now_correct = torch.max(F.softmax(output, dim=-1), dim=-1)[1]
            now_correct = torch.sum(now_correct == label).item()
            correct += now_correct
            s += len(label)
            batch_num += 1
            
            pbar.set_description(f'[!] aspect: {aspect}; loss: {round(loss.item(), 4)}; acc(run|overall): {round(now_correct/len(label), 4)}|{round(correct/s, 4)}')
        print(f'[!] overall acc: {round(correct/s, 4)}')
        return round(total_loss / batch_num, 4), round(correct/s, 4)
    
    def aggregation_strategy(self, outputs, s=0):
        def output_multiply_weighted(outputs, weighted):
            output = outputs[0] * weighted[0] + \
                     outputs[1] * weighted[1] + \
                     outputs[2] * weighted[2] + \
                     outputs[3] * weighted[3] + \
                     outputs[4] * weighted[4]
            return output
            
        if s == 0:
            output = outputs[0]    # only fluency
        elif s == 1:
            output = outputs[1]    # only coherence
        elif s == 2:
            output = outputs[2]    # only diversity
        elif s == 3:
            output = outputs[3]    # only naturalness
        elif s == 4:
            output = outputs[4]    # only relatedness
        elif s == 5:
            # average score
            output = torch.stack(outputs).mean(dim=0)
        elif s == 6:
            # min score
            output = torch.stack(outputs).min(dim=0)[0]
        elif s == 7:
            # max core
            output = torch.stack(outputs).max(dim=0)[0]
        elif s == 8:
            # weighted strategy 1
            weighted = [0.1, 0.3, 0.1, 0.25, 0.25]
            output = output_multiply_weighted(outputs, weighted)
        elif s == 9:
            # weighted strategy 2
            weighted = [0.1, 0.6, 0.1, 0.1, 0.1]
            output = output_multiply_weighted(outputs, weighted)
        else:
            raise Exception(f'[!] unknow aggregation strategy: {s}')
        return output
    
    @torch.no_grad()
    def test_model(self, test_iter, path, s=5):
        self.model.eval()
        pbar = tqdm(test_iter)
        rest = []
        with torch.no_grad():
            for idx, batch in enumerate(pbar):
                cid, label = batch
                outputs = self.model(cid, aspect='overall')
                
                outputs = [F.softmax(output, dim=-1)[:, 1] for output in outputs]
                output = self.aggregation_strategy(outputs, s=s)

                preds = [i.tolist() for i in torch.split(output, self.args['samples'])]
                labels = [i.tolist() for i in torch.split(label, self.args['samples'])]
                for label, pred in zip(labels, preds):
                    pred = np.argsort(pred, axis=0)[::-1]
                    rest.append(([0], pred.tolist()))
        p_1, r2_1, r10_1, r10_2, r10_5, MAP, MRR = cal_ir_metric(rest)
        print(f'[TEST] P@1: {p_1}; R2@1: {r2_1}; R10@1: {r10_1}; R10@2: {r10_2}; R10@5: {r10_5}; MAP: {MAP}; MRR: {MRR}')
        return 0
    
    def talk(self, topic, msgs, s=0):
        with torch.no_grad():
            # retrieval and process
            utterances_, ids = self.process_utterances(topic, msgs)
            # rerank, ids: [batch, seq]
            outputs = self.model(ids, aspect='overall')    # 5*[batch, 2]
            outputs = [F.softmax(output, dim=-1)[:, 1] for output in outputs]    # 5*[batch]
            # combine these scores
            output = self.aggregation_strategy(outputs, s=s)
            item = torch.argmax(output).item()
            msg = utterances_[item]
            return msg